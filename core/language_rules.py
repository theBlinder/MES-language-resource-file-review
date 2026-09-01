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


def check_entry(value, lang="en", glossary=None):
    """Run every check independently. Returns {category: [issue strings]}.
    A category with an empty list means that check found nothing wrong -
    it does NOT mean the check didn't run.
    `glossary` should be the SAME merged glossary (defaults + glossary.json)
    used to build the Suggested Change text, so detection and fixing never
    drift apart - pass None only to fall back to the built-in defaults."""
    issues = {"Spacing": [], "Grammar": [], "Spelling": [], "Terminology": []}
    v = value

    # Resolved once and reused by both the Terminology and Grammar sections
    # below - Terminology needs it to find glossary mismatches, Grammar
    # needs it to know which capitalized words are protected abbreviations
    # (e.g. "IDH", "DRL") rather than genuine casing inconsistencies.
    from core.engine import DEFAULT_GLOSSARY, PASSIVE_VOICE_FIXES, _ID_IDENTIFIER_RE
    active_glossary = glossary if glossary is not None else DEFAULT_GLOSSARY

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
    if lang in ("en", "unknown"):
        if re.search(r"No\.Of\b", v):
            issues["Grammar"].append('"No.Of" should be "No. of" (missing space, and "of" should be lowercase)')
        elif re.search(r"No\.\s*Of\b", v):
            issues["Grammar"].append('"Of" should be lowercase: "No. of", not "No. Of"')
    # Don't flag a custom-dictionary term with deliberate internal casing
    # (e.g. "iPAS", "macOS") as "doesn't start with a capital" just because
    # it happens to start with a lowercase letter - that casing is correct
    # and intentional. Must match the same skip in engine.py's fix_value,
    # or detection and fixing disagree on whether this string has an issue.
    from core.spellcheck import get_mixed_case_custom_words

    # A lowercase letter starting a NEW sentence - either the very start of
    # the string, or right after a real sentence boundary (". "/"! "/"? ") -
    # is a capitalization issue. Only position 0 needs the
    # "looks_like_sentence" gate (a short 1-2 word label starting lowercase
    # may be intentional, e.g. a UI hint); a position right after an
    # already-confirmed ". "/"! "/"? " boundary is unambiguous on its own,
    # so every such position is checked regardless of overall string length.
    # BUG FIX: this used to only ever look at v[0] - a lowercase letter
    # starting the SECOND (or later) sentence of a multi-sentence string
    # (e.g. "Confirm the order. please proceed") was never even detected.
    looks_like_sentence = len(v.split()) >= 3 or v.rstrip().endswith((".", "!", "?", ":"))
    if lang in ("en", "unknown"):
        for m in re.finditer(r"(?:^|[.!?]\s+)([A-Za-z])", v):
            start = m.start(1)
            if not m.group(1).islower():
                continue
            if start == 0 and not looks_like_sentence:
                continue
            word_match = re.match(r"[A-Za-z][A-Za-z0-9']*", v[start:])
            word = word_match.group(0) if word_match else ""
            if word in get_mixed_case_custom_words():
                continue
            issues["Grammar"].append("Sentence does not start with a capital letter")

    # A word capitalized right after a mid-sentence comma is almost always
    # wrong in this kind of UI text - real examples found in this file:
    # "..., Please contact administrator", "..., Process order cannot be
    # started". Scoped to a curated list of common instructional words to
    # avoid flagging genuine proper nouns after a comma.
    if lang in ("en", "unknown"):
        _CONTINUATION_WORDS = {
            "please", "process", "add", "check", "click", "select", "enter",
            "confirm", "contact", "ensure", "note", "review", "verify",
            "wait", "try", "refresh", "update", "save", "choose", "the",
            "this", "as", "and", "but",
        }
        for m in re.finditer(r",\s+([A-Z][a-z]+)\b", v):
            if m.group(1).lower() in _CONTINUATION_WORDS:
                issues["Grammar"].append(
                    f'"{m.group(1)}" should be lowercase after a mid-sentence comma: "...,  {m.group(1).lower()}..."'
                )

    # A string that mixes Title Case and sentence case internally (some
    # content words capitalized, some not, with no consistent pattern) is a
    # real find from this team's actual files - e.g. "No Images found",
    # "Quantity reverted Successfully". Conservative: only fires when BOTH
    # patterns appear among content words (excludes minor words: of/the/a/
    # in/to/and/or/is/are/was/were/by/on/at/as/per/vs/nor/but). A string
    # written ENTIRELY in Title Case (every eligible word capitalized, no
    # mix) is flagged separately below - a normal UI sentence/message should
    # use sentence case, only the first word and any defined multi-word
    # terminology capitalized (see `_protected_phrase_spans` in
    # core/engine.py) - real example: "Oven Capacity Limit Exceeds the
    # Maximum Number of Baking Activities Allowed in Parallel for Selected
    # Oven".
    if lang in ("en", "unknown"):
        _MINOR_WORDS = {"of", "the", "a", "an", "in", "to", "and", "or", "is",
                         "are", "was", "were", "by", "on", "at", "as", "per",
                         "vs", "nor", "but", "for"}
        # Only meaningful for single-phrase strings (labels, short messages) -
        # a multi-sentence string legitimately capitalizes the start of each
        # new sentence (e.g. "...recipe. Please contact administrator" is
        # CORRECT, not inconsistent), so skip whenever a sentence boundary
        # (. ! ? followed by a capital letter) is present.
        has_multiple_sentences = bool(re.search(r"[.!?]\s+[A-Z]", v))
        v_for_case_check = re.sub(r"\bNo\.\s*", "", v)
        from core.engine import _protected_phrase_spans, _in_span
        phrase_spans = _protected_phrase_spans(v_for_case_check, active_glossary)
        content_matches = [m for m in re.finditer(r"[A-Za-z']+", v_for_case_check)
                            if m.group(0).lower() not in _MINOR_WORDS and len(m.group(0)) > 1]
        if len(content_matches) >= 3 and not has_multiple_sentences:
            # skip the first word (sentence/title start is always capitalized either way)
            rest_matches = content_matches[1:]
            # An ALL-CAPS acronym (e.g. "LAB"), a protected custom-
            # dictionary/glossary term (e.g. "Vegam", "DRL"), or a word that's
            # part of a defined multi-word terminology phrase (e.g. "Order"
            # inside "Order Number") is not evidence of a real casing
            # inconsistency either way - excluded from the vote entirely,
            # same exemption `_fix_mixed_case_casing` in engine.py uses, so
            # detection and fixing never disagree on what counts as
            # "inconsistent" here.
            from core.spellcheck import get_custom_words
            custom_words = get_custom_words()
            protected_terms = {k.lower() for k in active_glossary if " " not in k} | \
                {str(x).lower() for k, x in active_glossary.items() if " " not in k}

            def _is_exempt(w):
                return w.isupper() or w.lower() in custom_words or w.lower() in protected_terms

            votable = [m.group(0) for m in rest_matches
                       if not _is_exempt(m.group(0)) and not _in_span(m.start(), phrase_spans)]
            has_cap = any(w[:1].isupper() and not w.isupper() for w in votable)
            has_lower = any(w[:1].islower() for w in votable)
            if has_cap and has_lower:
                issues["Grammar"].append(
                    "Inconsistent capitalization - mixes Title Case and sentence case within the same string"
                )
            elif has_cap and not has_lower:
                issues["Grammar"].append(
                    "Sentence is written in Title Case - normal UI sentences should use sentence case "
                    "(capitalize only the first word and any defined terminology)"
                )

    words = v.split()
    for i in range(len(words) - 1):
        if "[" in words[i] or "[" in words[i + 1]:
            continue
        w1 = re.sub(r"[^A-Za-z]", "", words[i]).lower()
        w2 = re.sub(r"[^A-Za-z]", "", words[i + 1]).lower()
        if w1 and len(w1) > 2 and w1 == w2:
            issues["Grammar"].append(f'Repeated word: "{words[i]} {words[i+1]}"')

    # Passive-voice noun-for-verb mixups (e.g. "been handovers" - a real
    # dictionary word used in the wrong grammatical slot, so spelling never
    # catches it). See PASSIVE_VOICE_FIXES in core/engine.py.
    if lang in ("en", "unknown"):
        for pattern, repl in PASSIVE_VOICE_FIXES.items():
            m = re.search(pattern, v, flags=re.IGNORECASE)
            if m:
                corrected = re.sub(pattern, repl, m.group(0), flags=re.IGNORECASE)
                issues["Grammar"].append(f'"{m.group(0)}" should be "{corrected}"')

    # ---------------- Terminology (glossary, language-specific lists) ----
    # `active_glossary` was resolved once at the top of this function (BUG
    # FIX, found 2026-08-25: this used to re-import and use the hardcoded
    # 8-entry DEFAULT_GLOSSARY here regardless of what was actually passed
    # in, so a custom glossary.json entry could appear in the fixed
    # "Suggested Change" text but never get counted as a Terminology issue
    # for ROUTING purposes - verified directly, "idh label missing..."
    # showed 0 Terminology issues even with "idh" in glossary.json).
    if lang in ("en", "unknown"):
        for wrong, canon in active_glossary.items():
            if re.search(rf"\b{re.escape(wrong)}\b", v, flags=re.IGNORECASE) and canon not in v:
                issues["Terminology"].append(f'"{wrong}" should be "{canon}"')

        # Phrase-level consistency: same file uses "No. of X" AND "number of
        # X" for the same concept in different strings. Verified against
        # this team's real file: "number of" is the majority form (17 vs 8
        # occurrences), so that's the canonical direction here.
        if re.search(r"\bNo\.\s*of\b", v, flags=re.IGNORECASE):
            issues["Terminology"].append(
                '"No. of" should be "number of" for consistency (the majority phrasing in this file)'
            )

        # camelCase identifiers ending in "ID" (e.g. "formulaID",
        # "materialID", "barcodeID") are two words glued together with no
        # space - see _split_id_identifiers in core/engine.py, which this
        # must stay in sync with so detection and fixing never disagree.
        for m in re.finditer(_ID_IDENTIFIER_RE, v):
            split_form = f"{m.group(1)} ID"
            canonical = active_glossary.get(split_form.lower(), split_form)
            if canonical not in v:
                issues["Terminology"].append(f'"{m.group(0)}" should be "{canonical}"')

    # ---------------- Spelling (real dictionary check via Hunspell) --------
    # Glossary keys (e.g. "ccp", "idh") are known abbreviations, not
    # misspellings - Terminology above already owns correcting their casing.
    # Without this, a word like "ccp" got flagged as BOTH a Terminology
    # issue ("ccp" should be "CCP") AND a Spelling issue with an unrelated,
    # nonsensical dictionary guess (e.g. "cp"), since spellcheck had no idea
    # it was a recognized domain term.
    from core.spellcheck import check_spelling, get_custom_words
    spelling_skip_words = get_custom_words() | {k.lower() for k in active_glossary}
    spelling_hits = check_spelling(v, lang, spelling_skip_words)
    if spelling_hits is None:
        pass  # dictionary not available for this language - not checked, not "clean"
    else:
        for word, suggestions, _confident in spelling_hits:
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
