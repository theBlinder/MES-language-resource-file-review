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


def _try_camelcase_split(word, lib, h):
    """Words like 'PalletInfo' are two real words concatenated with no space
    - hunspell can't recognize the compound and its raw suggestion is often
    an unrelated real word (e.g. 'Pallette'). If splitting at the first
    lowercase->uppercase boundary yields two valid dictionary words, that
    split IS the fix - far more reliable than a generic suggestion here."""
    m = re.search(r"[a-z][A-Z]", word)
    if not m:
        return None
    split_at = m.start() + 1
    left, right = word[:split_at], word[split_at:]
    if lib.Hunspell_spell(h, left.encode("utf-8")) and lib.Hunspell_spell(h, right.encode("utf-8")):
        return f"{left} {right}"
    return None


def _try_lowercase_compound_split(word, lib, h):
    """Same idea as the camelCase split, but for all-lowercase concatenations
    like 'taginfo' (tag + info) that have no capital-letter boundary to hint
    at the split point. Tries every split point, requires both halves to be
    real dictionary words of at least 3 letters (avoids nonsense splits like
    't' + 'aginfo'). Only used as a fallback when camelCase splitting found
    nothing, and only for words long enough that a real 2-word split makes
    sense (6+ letters)."""
    if len(word) < 6 or not word.islower():
        return None
    for i in range(3, len(word) - 2):
        left, right = word[:i], word[i:]
        if lib.Hunspell_spell(h, left.encode("utf-8")) and lib.Hunspell_spell(h, right.encode("utf-8")):
            return f"{left} {right}"
    return None


_WORD_RE = re.compile(r"[A-Za-z']+")

# Missing-apostrophe contractions are extremely common in typed text and a
# generic dictionary has no way to know "dont" means "don't" rather than
# genuinely being close to "font"/"dint"/etc (same edit distance, same
# length - no statistical heuristic distinguishes them). Handle this whole
# class of typo directly instead of relying on hunspell's raw suggestions.
CONTRACTION_FIXES = {
    "dont": "don't", "cant": "can't", "wont": "won't", "isnt": "isn't",
    "wasnt": "wasn't", "arent": "aren't", "doesnt": "doesn't", "didnt": "didn't",
    "havent": "haven't", "hasnt": "hasn't", "hadnt": "hadn't",
    "shouldnt": "shouldn't", "wouldnt": "wouldn't", "couldnt": "couldn't",
    "youre": "you're", "theyre": "they're", "ive": "I've",
    "youve": "you've", "theyve": "they've", "im": "I'm",
    # NOTE: "were" deliberately excluded - it's a real, common word on its
    # own ("they were happy"), so mapping it to "we're" would be wrong most
    # of the time. Only include contraction typos with no standalone meaning.
}

# "Real word, but almost certainly the WRONG real word in this context" -
# these pass hunspell's check cleanly (they're valid dictionary words) so the
# normal "is this misspelled" gate never even looks at them. Found via a real
# reported example (MRG, 2026-08-25): "caned" ("to cane" - punish/weave) in
# "caned pre-process bin" is clearly meant to be "canned". Kept deliberately
# tiny and exact-match only, same discipline as CONTRACTION_FIXES above - do
# NOT turn this into a general "flag uncommon real words" heuristic, that's
# exactly the kind of broad guess Lessons 1 and 6 warn against. Only add an
# entry here when it's a specific, reported, verified case.
REAL_WORD_TYPO_FIXES = {
    "caned": "canned",
}


def _rerank(word, suggestions):
    """Real hunspell's own ranking is already good (verified: 'receipe' ->
    'recipe' first, 'Falied' -> 'Failed' first, with no help needed) - do NOT
    reorder by length/edit-distance, an earlier attempt at that broke
    otherwise-correct results (e.g. reordered 'recipe' behind 'receipt').
    Only demerit suggestions that insert a space/hyphen (e.g. 'Fa lied'),
    which are rarely what's wanted as the top pick; keep everything else in
    hunspell's original relative order."""
    return sorted(suggestions, key=lambda s: 1 if (" " in s or "-" in s) else 0)


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


_INFLECTION_SUFFIXES = ("ing", "ed", "es", "s", "er", "ers")


def _matches_custom_word(word_lower, custom_words):
    """Exact match, OR word is a custom term plus a common inflectional
    suffix (preweigh -> preweighed/preweighing/preweighs) - the earlier
    exact-only version let 'preweigh' through but still flagged
    'Preweighed', which is the same real word, just conjugated."""
    if word_lower in custom_words:
        return True
    for suf in _INFLECTION_SUFFIXES:
        if word_lower.endswith(suf) and word_lower[: -len(suf)] in custom_words:
            return True
    return False


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
        # Strip leading/trailing quote-apostrophes used as quotation marks
        # (e.g. 'Confirm' meaning the button labeled Confirm) - but keep
        # internal apostrophes from real contractions (don't, user's).
        word = m.group(0).strip("'")
        if len(word) < 3 or word.isupper():
            continue  # skip short tokens/abbreviations like "IP", "OK"
        if _matches_custom_word(word.lower(), custom_words):
            continue  # known domain term (or its inflected form) - not a misspelling
        real_word_fix = REAL_WORD_TYPO_FIXES.get(word.lower())
        if real_word_fix:
            # Checked BEFORE the hunspell gate below, on purpose: these are
            # valid dictionary words (hunspell would say "ok" and never flag
            # them), which is exactly why a generic dictionary check can't
            # catch this class of typo. See REAL_WORD_TYPO_FIXES above.
            results.append((word, [real_word_fix]))
            continue
        ok = lib.Hunspell_spell(h, word.encode("utf-8"))
        if not ok:
            contraction = CONTRACTION_FIXES.get(word.lower())
            if contraction:
                # Known missing-apostrophe contraction - use directly,
                # skip hunspell's raw suggestions entirely (they're
                # unreliable for exactly this typo class, see note above).
                results.append((word, [contraction]))
                continue
            camel_split = _try_camelcase_split(word, lib, h)
            if camel_split:
                results.append((word, [camel_split]))
                continue
            lower_split = _try_lowercase_compound_split(word, lib, h)
            if lower_split:
                results.append((word, [lower_split]))
                continue
            slst = ctypes.POINTER(ctypes.c_char_p)()
            n = lib.Hunspell_suggest(h, ctypes.byref(slst), word.encode("utf-8"))
            suggestions = [slst[i].decode("utf-8") for i in range(n)]
            lib.Hunspell_free_list(h, ctypes.byref(slst), n)
            results.append((word, _rerank(word, suggestions)[:3]))
    return results


def _match_case(original, suggestion):
    """Apply original's case pattern to suggestion, so a lowercase misspelling
    doesn't get replaced by a suggestion that happens to be capitalized in
    the dictionary (or vice versa) - prevents unexpected mid-sentence
    capitalization changes."""
    if original.isupper():
        return suggestion.upper()
    if original[:1].isupper() and original[1:].islower():
        return suggestion[:1].upper() + suggestion[1:].lower()
    if original.islower():
        return suggestion.lower()
    return suggestion  # mixed/unusual casing - leave suggestion as-is


def _is_confident(word, suggestion):
    """Same guard as the JS side: don't auto-apply a suggestion that's
    wildly different in length from the original (e.g. 'dont' -> 'font' -
    both real words, same length, but no actual relationship in meaning).
    Blunt, but prevents context-destroying silent substitutions."""
    if " " in suggestion or "-" in suggestion:
        return False
    return abs(len(word) - len(suggestion)) <= 2


def fix_spelling(value, lang, custom_words=None):
    """Returns value with each misspelled word replaced by its top suggestion
    (word-boundary match, first/best suggestion only, case-matched to the
    original, and ONLY when the suggestion passes a basic confidence check).
    Returns original value unchanged if the dictionary isn't available or
    nothing needs fixing."""
    misspellings = check_spelling(value, lang, custom_words)
    if not misspellings:
        return value
    fixed = value
    for word, suggestions in misspellings:
        if suggestions and _is_confident(word, suggestions[0]):
            fixed = re.sub(rf"\b{re.escape(word)}\b", _match_case(word, suggestions[0]), fixed)
    return fixed
