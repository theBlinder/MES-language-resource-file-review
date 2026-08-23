"""
Real spelling checks using Hunspell (the same spell-check engine behind
LibreOffice/Firefox/Chrome), via ctypes against the system libhunspell -
no network call needed, works fully offline once the OS-level dictionary
package is installed.

Per-language dictionary availability is intentionally explicit: if a
language's dictionary isn't installed, we say so rather than silently
skipping spelling checks and letting everything land in "Clean".
"""
import ctypes
import os
import re

# filename/OS package hints - extend as you install more languages.
# Debian/Ubuntu: sudo apt-get install hunspell-<code>
# macOS (brew): brew install hunspell && download the .dic/.aff for the language
LANG_DICT_PATHS = {
    "en": ("/usr/share/hunspell/en_US.aff", "/usr/share/hunspell/en_US.dic"),
    "de": ("/usr/share/hunspell/de_DE.aff", "/usr/share/hunspell/de_DE.dic"),
    "fr": ("/usr/share/hunspell/fr.aff", "/usr/share/hunspell/fr.dic"),
    "es": ("/usr/share/hunspell/es_ES.aff", "/usr/share/hunspell/es_ES.dic"),
    "it": ("/usr/share/hunspell/it_IT.aff", "/usr/share/hunspell/it_IT.dic"),
    "pt": ("/usr/share/hunspell/pt_PT.aff", "/usr/share/hunspell/pt_PT.dic"),
    # Chinese/Japanese are NOT well served by hunspell (no word-level
    # "misspelling" concept the way space-delimited languages have it) -
    # see note in README. Left out on purpose rather than faked.
}

_LIB_PATHS = [
    "/usr/lib/x86_64-linux-gnu/libhunspell-1.7.so.0",
    "/usr/lib/libhunspell-1.7.so.0",
    "libhunspell-1.7.so.0",
]

_handles = {}  # lang -> Hunhandle
_lib = None


def _load_lib():
    global _lib
    if _lib is not None:
        return _lib
    for p in _LIB_PATHS:
        try:
            _lib = ctypes.CDLL(p)
            break
        except OSError:
            continue
    if _lib is None:
        return None
    _lib.Hunspell_create.restype = ctypes.c_void_p
    _lib.Hunspell_create.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    _lib.Hunspell_spell.restype = ctypes.c_int
    _lib.Hunspell_spell.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    _lib.Hunspell_suggest.restype = ctypes.c_int
    _lib.Hunspell_suggest.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_char_p)), ctypes.c_char_p]
    _lib.Hunspell_free_list.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_char_p)), ctypes.c_int]
    return _lib


def is_available(lang):
    """True only if we actually have both the library and this language's dictionary."""
    if lang not in LANG_DICT_PATHS:
        return False
    aff, dic = LANG_DICT_PATHS[lang]
    return _load_lib() is not None and os.path.exists(aff) and os.path.exists(dic)


def _get_handle(lang):
    if lang in _handles:
        return _handles[lang]
    if not is_available(lang):
        return None
    lib = _load_lib()
    aff, dic = LANG_DICT_PATHS[lang]
    h = lib.Hunspell_create(aff.encode(), dic.encode())
    _handles[lang] = h
    return h


_WORD_RE = re.compile(r"[A-Za-z']+")


def load_custom_words(path=None):
    """Domain-specific terms that are NOT real dictionary words but ARE
    correct for your product (e.g. 'preweigh', 'packoff') - skip these
    instead of flagging them. One word per line, case-insensitive."""
    words = set()
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    words.add(line.lower())
    return words


_custom_words_cache = None


def get_custom_words(path=None):
    """Cached loader so we don't re-read the file for every string."""
    global _custom_words_cache
    if _custom_words_cache is None:
        default_path = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "custom_dictionary.txt")
        _custom_words_cache = load_custom_words(default_path)
    return _custom_words_cache


def check_spelling(value, lang, custom_words=None):
    """Returns list of (word, [suggestions]) for words not found in the dictionary
    OR the custom word list. Skips placeholders like [XXX] and short/ALLCAPS
    tokens (likely abbreviations)."""
    if not is_available(lang):
        return None  # explicitly "not checked", different from "no errors"
    custom_words = custom_words or set()
    lib = _load_lib()
    h = _get_handle(lang)
    results = []
    for m in _WORD_RE.finditer(value):
        word = m.group(0)
        if len(word) < 3 or word.isupper():
            continue  # skip short tokens/abbreviations like "IP", "OK"
        if word.lower() in custom_words:
            continue  # known domain term - not a misspelling
        ok = lib.Hunspell_spell(h, word.encode("utf-8"))
        if not ok:
            slst = ctypes.POINTER(ctypes.c_char_p)()
            n = lib.Hunspell_suggest(h, ctypes.byref(slst), word.encode("utf-8"))
            suggestions = [slst[i].decode("utf-8") for i in range(n)]
            lib.Hunspell_free_list(h, ctypes.byref(slst), n)
            results.append((word, suggestions[:3]))
    return results


def fix_spelling(value, lang, custom_words=None):
    """Returns value with each misspelled word replaced by its top suggestion
    (word-boundary match, first/best suggestion only). Returns original value
    unchanged if the dictionary isn't available or nothing needs fixing."""
    misspellings = check_spelling(value, lang, custom_words)
    if not misspellings:
        return value
    fixed = value
    for word, suggestions in misspellings:
        if suggestions:
            fixed = re.sub(rf"\b{re.escape(word)}\b", suggestions[0], fixed)
    return fixed
