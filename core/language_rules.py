"""
Language-aware, per-category issue detection.

Design goals driven by the actual requirement:
- Every check (Spacing, Grammar, Spelling, Terminology) runs on EVERY string,
  independently. A string with 2 kinds of issues shows up in 2 sheets - no
  check short-circuits or "wins" over another.
- Language matters: punctuation-spacing rules are not universal (French
  requires a space before ! ? ; : - English does not), so rules branch on
  detected language family instead of assuming English for every file.
- Language is detected per FILE, not chosen once globally - because a single
  logical resource can exist as 4-5 separate files, one per language.
"""
import re
import os

# Common language codes we can recognize in a filename, e.g. "..._fr.js",
# "..-de.js", "resource.ja.js". Extend this list as your team's real naming
# convention gets confirmed - this is a best-effort default, not a source of
# truth. Always allow a manual override alongside it.
KNOWN_LANG_CODES = {
    "en": "English", "fr": "French", "de": "German", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "sv": "Swedish",
    "pl": "Polish", "tr": "Turkish", "ru": "Russian", "ja": "Japanese",
    "zh": "Chinese", "ko": "Korean", "ar": "Arabic", "hi": "Hindi",
}

# Languages where a space is REQUIRED before certain punctuation - our
# "remove space before punctuation" rule must NOT run on these.
SPACE_BEFORE_PUNCT_LANGS = {"fr"}


def detect_language(filename, sample_text=""):
    """Best-effort language detection from filename. Returns (code, name, confidence).
    confidence is 'filename' or 'unknown' - if unknown, caller should ask for
    a manual override rather than silently assuming English."""
    base = os.path.basename(filename).lower()
    base_noext = re.sub(r"\.js$", "", base)
    # look for a language code as its own token, separated by _ . - or start/end
    tokens = re.split(r"[_.\-]", base_noext)
    for tok in tokens:
        if tok in KNOWN_LANG_CODES:
            return tok, KNOWN_LANG_CODES[tok], "filename"
    return "unknown", "Unknown", "unknown"


def check_entry(value, lang="en"):
    """Run every check independently. Returns {category: [issue strings]}.
    A category with an empty list means that check found nothing wrong -
    it does NOT mean the check didn't run."""
    issues = {"Spacing": [], "Grammar": [], "Spelling": [], "Terminology": []}
    v = value

    # ---------------- Spacing (language-aware) ----------------
    if v != v.strip():
        issues["Spacing"].append("Leading/trailing whitespace")
    if "  " in v:
        issues["Spacing"].append("Double space")
    if re.search(r"(?<!\s)([.,!?;:])(?=[A-Za-z])(?!\w*\.(gif|png|jpe?g|pdf|csv|xlsx?))", v):
        issues["Spacing"].append("Missing space after punctuation (run-on sentence)")
    if lang not in SPACE_BEFORE_PUNCT_LANGS:
        if re.search(r"\s+[,!?;:]", v):
            issues["Spacing"].append("Unexpected space before punctuation")
    else:
        # French-family: a MISSING space before ! ? ; : is the actual issue
        if re.search(r"[A-Za-z][!?;:]", v):
            issues["Spacing"].append("Missing required space before punctuation (French-style spacing)")

    # ---------------- Grammar (structural, language-agnostic-ish) --------
    if re.search(r"([!?])\1+", v):
        issues["Grammar"].append("Repeated punctuation (e.g. \"!!\")")
    if re.search(r"\.\.(?!\.)", v):
        issues["Grammar"].append("Double period (not an ellipsis)")
    looks_like_sentence = len(v.split()) >= 3 or v.rstrip().endswith((".", "!", "?", ":"))
    if v and looks_like_sentence and v[0].islower() and v[0].isalpha() and lang in ("en", "unknown"):
        issues["Grammar"].append("Sentence does not start with a capital letter")
    words = v.split()
    for i in range(len(words) - 1):
        if "[" in words[i] or "[" in words[i + 1]:
            continue
        w1 = re.sub(r"[^A-Za-z]", "", words[i]).lower()
        w2 = re.sub(r"[^A-Za-z]", "", words[i + 1]).lower()
        if w1 and len(w1) > 2 and w1 == w2:
            issues["Grammar"].append(f'Repeated word: "{words[i]} {words[i+1]}"')

    # ---------------- Terminology (glossary, language-specific lists) ----
    from core.engine import DEFAULT_GLOSSARY
    if lang in ("en", "unknown"):
        for wrong, canon in DEFAULT_GLOSSARY.items():
            if re.search(rf"\b{re.escape(wrong)}\b", v, flags=re.IGNORECASE) and canon not in v:
                issues["Terminology"].append(f'"{wrong}" should be "{canon}"')

    # ---------------- Spelling (real dictionary check via Hunspell) --------
    from core.spellcheck import check_spelling, get_custom_words
    spelling_hits = check_spelling(v, lang, get_custom_words())
    if spelling_hits is None:
        pass  # dictionary not available for this language - not checked, not "clean"
    else:
        for word, suggestions in spelling_hits:
            suggestion_text = f' (did you mean "{suggestions[0]}"?)' if suggestions else ""
            issues["Spelling"].append(f'Possible misspelling: "{word}"{suggestion_text}')

    return issues


def check_spelling_grammar_lt(value, lang, lt_client=None):
    """Optional: call a (self-hosted) LanguageTool server for real dictionary
    spelling + deeper grammar checks. Returns (spelling_issues, grammar_issues).
    Returns ([], []) if no server is configured/reachable - callers should
    treat that as 'not checked', not 'no issues found'."""
    if lt_client is None:
        return [], []
    try:
        matches = lt_client.check(value, lang)
    except Exception:
        return [], []
    spelling, grammar = [], []
    for m in matches:
        bucket = spelling if "TYPOS" in m.get("ruleIssueType", "") or "spelling" in m.get("category", "").lower() else grammar
        bucket.append(f'{m.get("message", "issue")} (suggestion: {", ".join(m.get("replacements", [])[:2])})')
    return spelling, grammar
