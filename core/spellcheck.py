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
    split IS the fix - far more reliable than a generic suggestion here.
    Requires each half to be at least 4 letters - a short fragment either
    side (e.g. 'en' + 'Ter' from 'enTer') is far more likely a single word
    with one stray capital letter than two real words glued together; see
    `_try_stray_capital_fix`, which is tried first and handles that case."""
    m = re.search(r"[a-z][A-Z]", word)
    if not m:
        return None
    split_at = m.start() + 1
    left, right = word[:split_at], word[split_at:]
    if len(left) < 4 or len(right) < 4:
        return None
    if lib.Hunspell_spell(h, left.encode("utf-8")) and lib.Hunspell_spell(h, right.encode("utf-8")):
        return f"{left} {right}"
    return None


def _try_adjacent_transposition(word, lib, h):
    """Swapping two adjacent letters (e.g. 'laod' -> 'load', 'teh' -> 'the')
    is one of the most common human typing errors, and unlike an arbitrary
    substitution it reuses every original letter - so when swapping ONE pair
    of neighboring letters yields a real dictionary word, that's a far more
    targeted, information-preserving explanation of the typo than a generic
    nearest-neighbor dictionary suggestion. Hunspell's own suggestion ranking
    is not frequency-aware, so it can easily surface an obscure real word
    ahead of the actual transposition target - verified directly (MRG's real
    reports, 2026-09-02): for 'laod', hunspell's raw suggestions rank 'lad'
    (a real but rare noun, one deletion away) ahead of 'load' (the obviously
    intended word, a transposition away); same root cause turned 'pelase'
    (meant to be 'please') into 'pelage' (a rare zoology term for an
    animal's fur). Checked BEFORE raw suggestions are ever consulted so it
    isn't shadowed by that ranking. Requires EXACTLY ONE valid swap, same
    ambiguity discipline as the compound-split helpers below - if two
    different swaps each produce a real word, there's no reliable way to
    pick, so this backs off and lets the normal suggestion path handle it.
    Keep in sync with tryAdjacentTransposition in docs/index.html."""
    chars = list(word)
    found = set()
    for i in range(len(chars) - 1):
        if chars[i] == chars[i + 1]:
            continue
        swapped = chars[:]
        swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]
        candidate = "".join(swapped)
        if lib.Hunspell_spell(h, candidate.encode("utf-8")):
            found.add(candidate)
    return next(iter(found)) if len(found) == 1 else None


def _try_stray_capital_fix(word, lib, h):
    """A word with an unexpected capital letter somewhere after the first
    character (e.g. 'enTer') is almost always a single word typed with a
    stray capital, not two words glued together. If lowercasing the whole
    word yields a real dictionary word, that is a far safer, more targeted
    fix than guessing a two-word split - checked before any split is
    attempted. Verified directly: without this, 'enTer' matched the
    camelCase-split boundary ('en' + 'Ter', both individually valid
    dictionary entries) and produced the nonsensical 'en Ter' instead of the
    obviously-intended 'enter'."""
    if len(word) < 2 or not any(c.isupper() for c in word[1:]):
        return None
    lowered = word.lower()
    if lib.Hunspell_spell(h, lowered.encode("utf-8")):
        return lowered
    return None


def _try_lowercase_compound_split(word, lib, h):
    """Same idea as the camelCase split, but for all-lowercase concatenations
    like 'taginfo' (tag + info) that have no capital-letter boundary to hint
    at the split point. Tries every split point, requires both halves to be
    real dictionary words of at least 3 letters (avoids nonsense splits like
    't' + 'aginfo'). Only used as a fallback when camelCase splitting found
    nothing, and only for words long enough that a real 2-word split makes
    sense (6+ letters).

    Only returns a split when exactly ONE split point is valid. A word like
    'groupset' has two: 'gro'+'upset' AND 'group'+'set' - with no way to
    know which one is intended, blindly picking the first one found (as this
    used to) produced the nonsensical 'gro upset'. When the split is
    ambiguous like this, the word is most likely a real compound term simply
    missing from the dictionary (see `custom_dictionary.txt`) rather than
    two words missing a space, so no split is offered at all."""
    if len(word) < 6 or not word.islower():
        return None
    candidates = []
    for i in range(3, len(word) - 2):
        left, right = word[:i], word[i:]
        if lib.Hunspell_spell(h, left.encode("utf-8")) and lib.Hunspell_spell(h, right.encode("utf-8")):
            candidates.append(f"{left} {right}")
    if len(candidates) == 1:
        return candidates[0]
    return None


# Common English derivational prefixes. A word not recognized as a single
# dictionary entry may still be a completely standard compound: one of these
# prefixes attached directly to a real base word (e.g. 'underdosed' = under +
# dosed, 'premixture' = pre + mixture, 'inprogress' = in + progress). This is
# a GENERAL rule, not a per-word list - it is what stops the checker from
# "correcting" this whole class of word into something meaning-changing
# (e.g. 'inprogress' -> 'progress', which silently drops the very prefix
# that carries the word's meaning) instead of leaving it alone. Sorted
# longest-first when matched so e.g. 'undermine' checks against 'under'
# before the shorter 'un' could apply.
_DERIVATIONAL_PREFIXES = tuple(sorted([
    "counter", "inter", "under", "over", "semi", "multi", "auto", "anti",
    "post", "non", "pre", "sub", "mis", "dis", "re", "un", "in", "co", "de",
], key=len, reverse=True))


def _is_valid_prefixed_compound(word, lib, h):
    """True if `word` is a recognized derivational prefix directly followed
    by a real dictionary word (see `_DERIVATIONAL_PREFIXES`) - i.e. it's
    standard English, just not present as its own dictionary entry, and
    should be treated as correctly spelled rather than flagged at all."""
    wl = word.lower()
    for p in _DERIVATIONAL_PREFIXES:
        if wl.startswith(p) and len(wl) - len(p) >= 3:
            rest = wl[len(p):]
            if lib.Hunspell_spell(h, rest.encode("utf-8")):
                return True
    return False


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
    # MRG's real reported examples (2026-09-01): "Formula already exits for
    # material", "Material does not exits", "Formula name already exits" -
    # "exits" (plural of the noun "exit", or the verb "to exit") is a real
    # dictionary word, but in every one of these UI-message contexts the
    # intended word is clearly "exists". Same discipline as "caned"/"pleas"/
    # "sech" above - one curated entry for a specific, verified case. Keep
    # in sync with REAL_WORD_TYPO_FIXES in docs/index.html.
    "exits": "exists",
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

# Generalizes ACCEPTED_SPELLING_VARIANTS above to an entire CLASS of
# spelling instead of one curated word at a time: a handful of well-
# established British/American (etc.) suffix alternations account for
# almost every "valid word, just the other region's spelling" case a
# generic en_US dictionary rejects - "-ogue"/"-og" (catalogue/catalog),
# "-our"/"-or" (colour/color), "-ise"/"-ize" (organise/organize), and so on.
# Verified directly (MRG's real report, 2026-09-02): "Catalogue" was being
# "corrected" to "Cataloged"/"Catalog" purely because hunspell's own
# suggestion ranking doesn't know it's a legitimate regional spelling, not a
# typo - same root cause as the original "cancelled" bug this pattern
# replaces the need for. Checked in _is_regional_spelling_variant below:
# if undoing one of these patterns on the word yields a REAL dictionary
# word, the original is already correctly spelled and must never be
# flagged. Keep in sync with REGIONAL_SUFFIX_PAIRS in docs/index.html.
REGIONAL_SUFFIX_PAIRS = [
    ("ogue", "og"), ("ogues", "ogs"),
    ("our", "or"), ("ours", "ors"),
    ("ise", "ize"), ("ised", "ized"), ("ising", "izing"), ("iser", "izer"),
    ("isers", "izers"), ("isation", "ization"), ("isations", "izations"),
    ("yse", "yze"), ("ysed", "yzed"), ("ysing", "yzing"), ("yser", "yzer"),
    ("tre", "ter"), ("tres", "ters"),
    ("ence", "ense"),
    ("lled", "led"), ("lling", "ling"), ("ller", "ler"),
]


def _is_regional_spelling_variant(word_lower, lib, h):
    """True if `word_lower` turns into a real dictionary word by undoing one
    of the REGIONAL_SUFFIX_PAIRS alternations above - i.e. it's standard
    English under a different region's spelling convention, not a typo, and
    should never be flagged or "corrected" toward the other region's form."""
    for a, b in REGIONAL_SUFFIX_PAIRS:
        if word_lower.endswith(a) and lib.Hunspell_spell(h, (word_lower[: -len(a)] + b).encode("utf-8")):
            return True
        if word_lower.endswith(b) and lib.Hunspell_spell(h, (word_lower[: -len(b)] + a).encode("utf-8")):
            return True
    return False


def _levenshtein(a, b):
    """Real edit distance (insert/delete/substitute), not just a length-diff
    proxy - needed to safely gate raw dictionary suggestions against a
    compound-word split below (see check_spelling): a length-diff check
    alone can't tell "relevent"->"relevant" (one substitution, genuinely
    close) apart from "taginfo"->"tagging" (same length, but a different
    word throughout)."""
    m, n = len(a), len(b)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cur[j] = prev[j - 1] if a[i - 1] == b[j - 1] else 1 + min(prev[j], cur[j - 1], prev[j - 1])
        prev = cur
    return prev[n]


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


_domain_words_cache = None


def get_domain_words_for_fuzzy_match(path=None):
    """Same source as get_custom_words, filtered to 4+ letter entries and
    cached as a list for repeated edit-distance scans (see
    _try_domain_fuzzy_match). Short entries (e.g. 'sscc', 'idh') are
    excluded: at edit-distance <= 2, almost any short typo of an unrelated
    word can coincidentally land within range of a 3-4 letter acronym, so
    fuzzy-matching them is more likely to misfire than help - they're still
    caught by the exact/inflected match in _matches_custom_word. Keep in
    sync with the same-purpose filter in docs/index.html."""
    global _domain_words_cache
    if _domain_words_cache is None:
        _domain_words_cache = [w for w in get_custom_words(path) if len(w) >= 4]
    return _domain_words_cache


def _try_domain_fuzzy_match(word_lower, domain_words, raw_top_distance):
    """custom_dictionary.txt words are real product vocabulary that a
    generic English dictionary can never suggest (it doesn't know them at
    all) - so a typo'd domain term (e.g. 'prewigh' for 'preweigh') only ever
    gets scored against unrelated real English words (e.g. 'prewash'),
    which can win hunspell's edit-distance gate purely because English
    happens to have a coincidentally close word. Verified directly (MRG's
    real report, 2026-09-02): 'Prewigh label not found' was being
    "corrected" to 'Prewash label not found' for exactly this reason, even
    though 'preweigh' (already in custom_dictionary.txt) is one insertion
    away and 'prewash' is two substitutions away. Comparing the typo
    against the product's own vocabulary too, and preferring a domain match
    ONLY when it's a STRICTLY closer fit than hunspell's own top guess (or
    hunspell has no guess at all), fixes this without special-casing any
    individual word - it generalizes to every current and future entry in
    custom_dictionary.txt. Requires the closest domain word to be uniquely
    closest (same ambiguity discipline as the compound-split helpers) - a
    tie between two domain words means there's no reliable pick. Keep in
    sync with tryDomainFuzzyMatch in docs/index.html."""
    best, best_dist, tie_count = None, None, 0
    for cw in domain_words:
        if abs(len(cw) - len(word_lower)) > 2:
            continue
        d = _levenshtein(word_lower, cw)
        if best_dist is None or d < best_dist:
            best, best_dist, tie_count = cw, d, 1
        elif d == best_dist:
            tie_count += 1
    if best_dist is not None and best_dist <= 2 and tie_count == 1 and best_dist < raw_top_distance:
        return best
    return None


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


_STRAY_QUOTE_WORD_RE = re.compile(r"[A-Za-z]+[\"‘’“”][A-Za-z]+")
_STRAY_QUOTE_CHAR_RE = re.compile(r"[\"‘’“”]")


def _fix_stray_quote_word(word):
    """Swap a mistyped mid-word quote character for a real apostrophe."""
    return _STRAY_QUOTE_CHAR_RE.sub("'", word)


def check_spelling(value, lang, custom_words=None):
    """Returns list of (word, [suggestions], confident) for words not found
    in the dictionary OR the custom word list. `confident` is True when the
    top suggestion came from a deterministic, high-confidence source (a
    known typo/contraction lookup, or a verified two-real-word split) and
    should be trusted for auto-applying regardless of suggestion length or
    whether it contains a space - unlike a raw dictionary suggestion, whose
    confidence still depends on how close it is to the original word (real
    edit distance <= 2, no inserted space/hyphen - see `_levenshtein` and
    the gate right before the compound-split attempts below). Skips
    placeholders like [XXX] (see `_mask_brackets`) and short/ALLCAPS tokens
    (likely abbreviations)."""
    if not is_available(lang):
        return None  # explicitly "not checked", different from "no errors"
    # Lazy import (avoids a circular import at module load time - core.engine
    # imports this module's functions the same way, lazily, inside its own
    # functions). A camelCase "<word>ID" identifier (e.g. "formulaID",
    # "materialID") is already fully owned by the Terminology check
    # (_split_id_identifiers in core/engine.py) - skip it here too, same
    # discipline as glossary abbreviation keys below. Without this, a raw
    # dictionary suggestion for the whole glued word (e.g. "formulaID" ->
    # "formula", edit-distance 2) could get reported as a misleading
    # "Words To Correct" entry that silently drops the "ID" suffix, even
    # though the actual "Suggested Change" text is unaffected (structural
    # fixes always run before spelling). Keep in sync with the same skip in
    # spellCheckValue in docs/index.html.
    from core.engine import _ID_IDENTIFIER_RE
    custom_words = custom_words or set()
    lib = _load_lib()
    h = _get_handle(lang)
    domain_words = get_domain_words_for_fuzzy_match()
    results = []

    # A straight double-quote or curly quote directly between two letters
    # (no surrounding whitespace) is virtually never a real quotation mark -
    # a genuine quoted word always has a space/punctuation/string boundary
    # next to the quote (e.g. 'Confirm' button). It's almost always the
    # apostrophe key mistyped (shift not held, since ' and " share a key on
    # most keyboards) or a curly quote substituted by autocorrect. Verified
    # directly (MRG's real report, 2026-09-02): 'doesn"t' used to be
    # extracted as two separate garbage tokens ('doesn' and 't', since '"'
    # isn't part of the word-character class), and 'doesn' alone then got
    # "corrected" to 'does' - silently dropping the 'n' and never even
    # reaching a real contraction fix, because the word never existed as a
    # single token in the first place. Handled as its own pre-pass (checked
    # unconditionally, like REAL_WORD_TYPO_FIXES) rather than by
    # normalizing the whole value up front: hunspell already accepts
    # "doesn't" as valid once the apostrophe is real, so a silent up-front
    # normalization would make the word look already-correct and this typo
    # would never be counted as an issue or shown as a fix at all. The
    # matched span is masked out of the copy used below so its fragments
    # aren't independently re-processed by the normal per-word loop. Keep
    # in sync with the same-purpose pass in spellCheckValue in
    # docs/index.html.
    masked = _mask_brackets(value)
    stray_quote_matches = [sq for sq in _STRAY_QUOTE_WORD_RE.finditer(masked) if not sq.group(0).isupper()]
    for sq in stray_quote_matches:
        word = sq.group(0)
        results.append((word, [_fix_stray_quote_word(word)], True))
    if stray_quote_matches:
        chars = list(masked)
        for sq in stray_quote_matches:
            for i in range(sq.start(), sq.end()):
                chars[i] = "#"
        masked = "".join(chars)

    for m in _WORD_RE.finditer(masked):
        # Strip leading/trailing quote-apostrophes used as quotation marks
        # (e.g. 'Confirm' meaning the button labeled Confirm) - but keep
        # internal apostrophes from real contractions (don't, user's).
        word = m.group(0).strip("'")
        if len(word) < 3 or word.isupper():
            continue  # skip short tokens/abbreviations like "IP", "OK"
        if _ID_IDENTIFIER_RE.fullmatch(word):
            continue  # camelCase "<word>ID" identifier - Terminology already owns this
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
            if _is_valid_prefixed_compound(word, lib, h):
                # Standard prefix + real base word (see
                # _is_valid_prefixed_compound) - correctly spelled as one
                # word, not a misspelling at all.
                continue
            if _is_regional_spelling_variant(word.lower(), lib, h):
                # Standard English under a different region's spelling
                # convention (see REGIONAL_SUFFIX_PAIRS) - correctly spelled,
                # not a misspelling at all.
                continue
            known_fix = CONTRACTION_FIXES.get(word.lower()) or KNOWN_TYPO_FIXES.get(word.lower())
            if known_fix:
                # Known contraction or hand-verified typo - use directly,
                # skip hunspell's raw suggestions entirely (they're
                # unreliable for exactly these typo classes, see notes above).
                results.append((word, [known_fix], True))
                continue
            stray_cap_fix = _try_stray_capital_fix(word, lib, h)
            if stray_cap_fix:
                results.append((word, [stray_cap_fix], True))
                continue
            transposition_fix = _try_adjacent_transposition(word, lib, h)
            if transposition_fix:
                results.append((word, [transposition_fix], True))
                continue

            # Compute the dictionary's own raw suggestions ONCE, before
            # trying any compound-word split - a split is only ever the
            # right fix when the word doesn't already have a close, ordinary
            # single-word correction. Checking this FIRST prevents a
            # coincidental valid two-word split from outranking a much more
            # likely single-word typo fix. Verified directly (MRG's real
            # report, 2026-09-01): "relevent" (a typo for "relevant", one
            # letter off) has exactly one valid compound split - "rel" +
            # "event", both real dictionary words - which used to win
            # outright and produce the nonsensical "rel event" instead of
            # the obviously-intended "relevant", because the splits were
            # tried before any raw suggestion was ever consulted. Gated on
            # real edit distance (not just length-diff, see
            # `_levenshtein`) so this doesn't regress the "PalletInfo" /
            # "taginfo" compound-split cases below, whose original words
            # aren't actually close to any single real word. Keep in sync
            # with spellCheckValue in docs/index.html.
            slst = ctypes.POINTER(ctypes.c_char_p)()
            n = lib.Hunspell_suggest(h, ctypes.byref(slst), word.encode("utf-8"))
            raw_suggestions = _rerank(word, [slst[i].decode("utf-8") for i in range(n)])[:3]
            lib.Hunspell_free_list(h, ctypes.byref(slst), n)
            raw_top_distance = _levenshtein(word.lower(), raw_suggestions[0].lower()) if raw_suggestions else float("inf")

            # Try the product's own vocabulary before accepting hunspell's
            # generic top guess - see _try_domain_fuzzy_match for why this
            # must run before the raw-suggestion gate below, not after.
            domain_fix = _try_domain_fuzzy_match(word.lower(), domain_words, raw_top_distance)
            if domain_fix:
                results.append((word, [domain_fix], True))
                continue

            if (raw_suggestions and " " not in raw_suggestions[0] and "-" not in raw_suggestions[0]
                    and raw_top_distance <= 2):
                results.append((word, raw_suggestions, True))
                continue

            camel_split = _try_camelcase_split(word, lib, h)
            if camel_split:
                results.append((word, [camel_split], True))
                continue
            lower_split = _try_lowercase_compound_split(word, lib, h)
            if lower_split:
                results.append((word, [lower_split], True))
                continue

            results.append((word, raw_suggestions, False))
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
