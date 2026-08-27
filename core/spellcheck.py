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
    # MRG's real reported example (2026-08-27): "...BOM, pleas check" -
    # "pleas" (plural of "plea", a real dictionary word) is clearly meant
    # to be "please" in this context. Same discipline as "caned" above -
    # one curated entry for one specific, verified case.
    "pleas": "please",
    # MRG's real reported example (2026-08-27): "Sech by mixer" - "sech" is
    # a real dictionary word (the hyperbolic secant function, a math term -
    # verified directly that hunspell's en_US dictionary accepts it and
    # therefore never flags it), but in this UI-label context it's clearly
    # meant to be "search". Same discipline as "caned"/"pleas" above - one
    # curated entry for one specific, verified case. Keep in sync with
    # REAL_WORD_TYPO_FIXES in docs/index.html.
    "sech": "search",
}

# Exact-typo -> definitely-correct-fix overrides, for cases where BOTH
# dictionary engines' automatic suggestion ranking gets it wrong (verified
# 2026-08-25, MRG's real reports). Same discipline as CONTRACTION_FIXES and
# REAL_WORD_TYPO_FIXES above - exact match only, one curated entry per
# specific reported case, never a general re-ranking heuristic (Lessons 1
# and 6). IMPORTANT: hunspell (Python) and typo-js (JS, used by the actual
# website) do NOT always agree on ranking for the same typo - "falied" was
# previously believed to rank "Failed" first everywhere (see the old note
# below), but that's only true for hunspell; typo-js ranks "flied" first for
# the exact same typo. Verify against BOTH engines before assuming either is
# fixed, and keep this table in sync with the same-named object in
# docs/index.html.
KNOWN_TYPO_FIXES = {
    "falied": "failed",
    # NOTE: "atleast" -> "at least" was here (2026-08-27 round 3) but was
    # explicitly REVERSED by MRG the same day: "let atleast be atleast do
    # not split that into 2 words." "atleast" is now instead added to
    # custom_dictionary.txt, which suppresses it from being flagged at all
    # - see Section 4 of PROJECT_ARCHIVE.md. Do not re-add a fix for this word
    # without a new, explicit instruction from MRG.
}

# Real, standard, CORRECTLY spelled English words that our bundled en_US
# dictionary doesn't recognize purely because they're a different regional
# spelling (British/Commonwealth vs. American) - NOT domain jargon, and
# deliberately NOT in custom_dictionary.txt, which is reserved for words
# that genuinely aren't standard English at all (see that file's own
# header). MRG was explicit about this distinction (2026-08-27): "cancelled
# ... is a real English word right, try to put it in the proper way" -
# objecting specifically to it being lumped in with domain-jargon custom
# words. These are treated as already fully correct - never flagged, never
# "corrected" to the American spelling. Root cause of the original bug:
# hunspell's OWN top raw suggestion for "cancelled" is actually "canceled"
# (verified directly) - but `_try_lowercase_compound_split` below runs
# BEFORE raw suggestions are ever consulted, and "can" + "celled" both pass
# as individually valid dictionary words, so the split wins first and wrong
# ("can celled"). Rather than reordering that lookup generally (risks
# regressing the "taginfo" -> "tag info" case, which relies on compound
# split running first - verified directly that swapping the order broadly
# breaks that case), this is a small, exact, curated exemption - same
# discipline as KNOWN_TYPO_FIXES/REAL_WORD_TYPO_FIXES/CONTRACTION_FIXES.
# Keep in sync with the same-named object in docs/index.html.
ACCEPTED_SPELLING_VARIANTS = {
    "cancelled",
}


def _rerank(word, suggestions):
    """Real hunspell's own ranking is USUALLY already good (verified:
    'receipe' -> 'recipe' first) - do NOT reorder by length/edit-distance,
    an earlier attempt at that broke otherwise-correct results (e.g.
    reordered 'recipe' behind 'receipt'). Only demerit suggestions that
    insert a space/hyphen (e.g. 'Fa lied'), which are rarely what's wanted
    as the top pick; keep everything else in hunspell's original relative
    order. For the residual cases where even this isn't enough (hunspell's
    -or- typo-js's own top pick is just wrong), see KNOWN_TYPO_FIXES above -
    that's checked first and skips this function entirely."""
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


_mixed_case_words_cache = None


def get_mixed_case_custom_words(path=None):
    """Original-case custom-dictionary entries that mix upper/lower letters
    in a specific, meaningful way (e.g. 'iPAS', 'HCode', 'macOS') - these
    need their EXACT casing preserved even at the start of a sentence, where
    the generic 'capitalize sentence start' fix would otherwise blindly
    uppercase just the first letter and corrupt them (e.g. 'iPAS' ->
    'IPAS'). Only entries that are neither all-lowercase nor all-uppercase
    qualify - plain words like 'Vegam' or 'kanban' don't need this (the
    normal capitalize-first-letter behavior is already correct for them,
    and shouldn't be suppressed). Found via MRG's explicit 'leave iPAS as
    is' instruction, 2026-08-25 - iPAS starting a sentence was silently
    becoming 'IPAS'. Keep in sync with the same-purpose set in
    docs/index.html."""
    global _mixed_case_words_cache
    if _mixed_case_words_cache is None:
        default_path = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "custom_dictionary.txt")
        words = set()
        if os.path.exists(default_path):
            with open(default_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and not line.islower() and not line.isupper():
                        words.add(line)
        _mixed_case_words_cache = words
    return _mixed_case_words_cache


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


_BRACKET_RE = re.compile(r"\[[^\]]*\]")


def _mask_brackets(value):
    """Replace [placeholder] spans with same-length '#' filler so nothing
    inside them is ever extracted as a checkable word - resource strings use
    [XXX], [BinCode], etc. for real values substituted in at runtime; these
    are not English text and must never be spell-checked or "corrected"
    (found via MRG's report, 2026-08-25). Same-length filler keeps every
    OTHER word's character position in the string unchanged."""
    return _BRACKET_RE.sub(lambda m: "#" * len(m.group(0)), value)


def check_spelling(value, lang, custom_words=None):
    """Returns list of (word, [suggestions], confident) for words not found
    in the dictionary OR the custom word list. `confident` is True when the
    top suggestion came from a deterministic, high-confidence source (a
    known typo/contraction lookup, or a verified two-real-word split) and
    should be trusted for auto-applying regardless of suggestion length or
    whether it contains a space - unlike a raw dictionary suggestion, whose
    confidence still depends on how close it is to the original word (see
    `_is_confident` below). Skips placeholders like [XXX] (see
    `_mask_brackets`) and short/ALLCAPS tokens (likely abbreviations)."""
    if not is_available(lang):
        return None  # explicitly "not checked", different from "no errors"
    custom_words = custom_words or set()
    lib = _load_lib()
    h = _get_handle(lang)
    results = []
    for m in _WORD_RE.finditer(_mask_brackets(value)):
        # Strip leading/trailing quote-apostrophes used as quotation marks
        # (e.g. 'Confirm' meaning the button labeled Confirm) - but keep
        # internal apostrophes from real contractions (don't, user's).
        word = m.group(0).strip("'")
        if len(word) < 3 or word.isupper():
            continue  # skip short tokens/abbreviations like "IP", "OK"
        if _matches_custom_word(word.lower(), custom_words):
            continue  # known domain term (or its inflected form) - not a misspelling
        if word.lower() in ACCEPTED_SPELLING_VARIANTS:
            continue  # standard English, just a different regional spelling - see ACCEPTED_SPELLING_VARIANTS above
        real_word_fix = REAL_WORD_TYPO_FIXES.get(word.lower())
        if real_word_fix:
            # Checked BEFORE the hunspell gate below, on purpose: these are
            # valid dictionary words (hunspell would say "ok" and never flag
            # them), which is exactly why a generic dictionary check can't
            # catch this class of typo. See REAL_WORD_TYPO_FIXES above.
            results.append((word, [real_word_fix], True))
            continue
        ok = lib.Hunspell_spell(h, word.encode("utf-8"))
        if not ok:
            known_fix = CONTRACTION_FIXES.get(word.lower()) or KNOWN_TYPO_FIXES.get(word.lower())
            if known_fix:
                # Known contraction or hand-verified typo - use directly,
                # skip hunspell's raw suggestions entirely (they're
                # unreliable for exactly these typo classes, see notes above).
                results.append((word, [known_fix], True))
                continue
            camel_split = _try_camelcase_split(word, lib, h)
            if camel_split:
                results.append((word, [camel_split], True))
                continue
            lower_split = _try_lowercase_compound_split(word, lib, h)
            if lower_split:
                results.append((word, [lower_split], True))
                continue
            slst = ctypes.POINTER(ctypes.c_char_p)()
            n = lib.Hunspell_suggest(h, ctypes.byref(slst), word.encode("utf-8"))
            suggestions = _rerank(word, [slst[i].decode("utf-8") for i in range(n)])[:3]
            lib.Hunspell_free_list(h, ctypes.byref(slst), n)
            confident = bool(suggestions) and _is_confident(word, suggestions[0])
            results.append((word, suggestions, confident))
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
    """Guard for a RAW dictionary suggestion only (i.e. hunspell's own
    ranked guess, not a curated lookup or a verified two-word split - those
    carry their own `confident=True` from check_spelling and skip this
    entirely). Don't auto-apply a raw suggestion that's wildly different in
    length from the original (e.g. 'dont' -> 'font' - both real words, same
    length, but no actual relationship in meaning), or that inserts a
    space/hyphen (raw multi-word suggestions are rarely right - Lesson 1).
    Blunt, but prevents context-destroying silent substitutions.

    BUG FIX (found 2026-08-25): this used to also be re-applied, via its
    space/hyphen check, to suggestions that ALREADY came from
    `_try_camelcase_split` / `_try_lowercase_compound_split` - which are
    two-word results BY DESIGN (e.g. "PalletInfo" -> "Pallet Info"). Since
    every multi-word suggestion always contains a space, this silently
    blocked those verified splits from ever reaching "Suggested Change" -
    they showed up correctly in the "Words To Correct" column but the
    string itself was never actually fixed. Verified directly: before this
    fix, `fix_spelling("PalletInfo needs review", ...)` returned the
    string UNCHANGED despite detecting the correct split. Confidence is now
    decided once, at the source, in check_spelling - this function is only
    consulted for the raw-hunspell-suggestion case."""
    if " " in suggestion or "-" in suggestion:
        return False
    return abs(len(word) - len(suggestion)) <= 2


def fix_spelling(value, lang, custom_words=None):
    """Returns value with each misspelled word replaced by its top suggestion
    (word-boundary match, first/best suggestion only, case-matched to the
    original, and ONLY when check_spelling marked it confident - see that
    function's docstring for what "confident" means per source).
    Returns original value unchanged if the dictionary isn't available or
    nothing needs fixing."""
    misspellings = check_spelling(value, lang, custom_words)
    if not misspellings:
        return value
    fixed = value
    for word, suggestions, confident in misspellings:
        if suggestions and confident:
            fixed = re.sub(rf"\b{re.escape(word)}\b", _match_case(word, suggestions[0]), fixed)
    return fixed
