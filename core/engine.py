"""
Core parsing / detection / fixing logic for `languageResource.KEY = "value";`
style JS localization resource files. Shared by the CLI (cli.py) and the
local web app (app.py) so there's exactly one place the rules live.
"""
import re
import json
import os

LINE_PATTERN = re.compile(
    r'^(?P<indent>\s*)languageResource\.(?P<key>[A-Za-z0-9_]+)\s*=\s*"(?P<value>(?:[^"\\]|\\.)*)"'
    r'(?P<semi>\s*;)?(?P<trail>\s*(//.*)?)\r?$'
)

DEFAULT_GLOSSARY = {
    "ip": "IP", "sop": "SOP", "hu": "HU", "po": "PO",
    "fg": "FG", "qc": "QC", "sscc": "SSCC", "drl": "DRL",
}

# Passive-voice constructions where a noun form is mistakenly typed in place
# of the correct past-participle verb phrase - e.g. "has already been
# handovers to AWETA" (the noun "handover(s)", not the verb phrase "handed
# over"). "handover(s)" is a valid dictionary word on its own (see
# core/spellcheck.py), so a generic spelling check never flags it - this is
# a grammatical-slot problem, not a spelling one. Curated, exact-pattern
# entries only - same discipline as REAL_WORD_TYPO_FIXES in
# core/spellcheck.py (a real word that is nonetheless the wrong word for
# this specific grammatical slot) - and scoped to the passive auxiliary
# immediately before the noun so a legitimate plural-noun use elsewhere
# (e.g. "View recent handovers") is never touched. Keys are regex patterns
# (case-insensitive), values are their re.sub replacement (may use
# backreferences). Keep in sync with PASSIVE_VOICE_FIXES in docs/index.html.
PASSIVE_VOICE_FIXES = {
    r"\b(been|being)\s+handovers?\b": r"\1 handed over",
}

# camelCase identifiers ending in "ID" (e.g. "formulaID", "materialID",
# "barcodeID") are really two words glued together with no space. Only
# fires when the character right before "ID" is lowercase - that's the
# camelCase join boundary; an all-caps prefix (e.g. "SSCCID") is left alone
# as ambiguous rather than guessed at. Keep in sync with ID_IDENTIFIER_RE in
# docs/index.html.
_ID_IDENTIFIER_RE = re.compile(r"\b([A-Za-z]*[a-z])ID\b")


def _split_id_identifiers(v):
    """Split a camelCase "<word>ID" identifier into "<word> ID" - run BEFORE
    the glossary phrase-substitution step in fix_value() so a defined
    canonical phrase (e.g. "barcode id" -> "Barcode ID") still applies
    correctly once the space exists; an identifier with no defined phrase
    (e.g. "formula ID") simply keeps its original leading-word casing, just
    space-separated."""
    return _ID_IDENTIFIER_RE.sub(lambda m: f"{m.group(1)} ID", v)

# File-extension-like tokens that must never be treated as "end of sentence".
_EXT_GUARD = r"(?!\w*\.(gif|png|jpe?g|pdf|csv|xlsx?))"

# Optional: real spelling/grammar via LanguageTool, only used if the package
# + a local LanguageTool server are available on the machine running this.
try:
    import language_tool_python
    _LT_AVAILABLE = True
except ImportError:
    _LT_AVAILABLE = False

_lt_tool = None


def get_language_tool(lang="en-US"):
    """Lazily start a local LanguageTool instance. Returns None if unavailable."""
    global _lt_tool
    if not _LT_AVAILABLE:
        return None
    if _lt_tool is None:
        _lt_tool = language_tool_python.LanguageTool(lang)
    return _lt_tool


def load_glossary(path=None):
    g = dict(DEFAULT_GLOSSARY)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            g.update(json.load(f))
    return g


def parse_entries(path):
    """Read a resource file and return a list of {line, key, value} dicts."""
    entries = []
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            m = LINE_PATTERN.match(raw.rstrip("\n"))
            if m:
                entries.append({
                    "line": lineno,
                    "key": m.group("key"),
                    "value": m.group("value"),
                })
    return entries


_MINOR_WORDS_CASING = {"of", "the", "a", "an", "in", "to", "and", "or", "is",
                        "are", "was", "were", "by", "on", "at", "as", "per",
                        "vs", "nor", "but", "for"}

# A run of 2+ words joined by slashes and/or hyphens with no space (e.g.
# "Hold/Resume", "On/Off", "Flash-Off") - a single meaningful compound
# UI action/label or domain term, not independent prose words. See
# _protected_phrase_spans below. Keep in sync with COMPOUND_WORD_RE in
# docs/index.html.
_COMPOUND_WORD_RE = re.compile(r"\b[A-Za-z]+(?:[/-][A-Za-z]+)+\b")

# A word immediately named as the target of a UI interaction (e.g. "click
# Confirm", "select the Retry option") is a reference to a literal
# button/control label, not ordinary prose - its capitalization is
# whatever that control is actually labeled, not a style choice this tool
# can second-guess. Found via testing beyond the originally reported
# "Hold/Resume" case: an UNQUOTED single-word reference like "click Confirm
# to proceed" was still getting its capital silently stripped by the mixed
# Title-case/sentence-case fix (treated as a lone Title-Case outlier in an
# otherwise-lowercase sentence) - a QUOTED reference ("click 'Confirm' to
# proceed") already happened to survive only by accident, because the
# leading quote character breaks the cap/lowercase regex checks, not by
# design. This closes that gap for the unquoted form too, and is the same
# underlying "meaningful UI/action combination" problem as
# _COMPOUND_WORD_RE above. Curated list of interaction verbs only - same
# discipline as _CONTINUATION_WORDS below. Keep in sync with
# ACTION_REFERENCE_RE in docs/index.html.
_ACTION_REFERENCE_RE = re.compile(
    r"\b(?:click|tap|press|select|choose|hit)\s+(?:on\s+|the\s+)?([A-Za-z][A-Za-z/-]*)\b",
    re.IGNORECASE,
)


def _protected_phrase_spans(v, glossary):
    """Character spans in `v` covered by a multi-word defined-terminology
    phrase from `glossary` (any key containing a space, e.g. "order number"
    -> "Order Number") - matched case-insensitively, longest phrase first so
    a longer defined term always wins over a shorter one it happens to
    contain (e.g. "Application Order" over a bare "Application"). A word
    inside one of these spans must never count as evidence of "inconsistent"
    or "Title Case" casing in the sentence-casing checks below - a defined
    multi-word term's casing is deliberate, not a style choice - and must
    never be recased by `_fix_mixed_case_casing`. It's safe to just leave
    these spans untouched there rather than actively re-casing them to their
    canonical form: the glossary substitution step later in fix_value()
    already handles multi-word phrases via this same word-boundary-regex
    mechanism and runs AFTER the sentence-casing fix, so it restores the
    exact canonical casing regardless of what (if anything) happened to the
    phrase's words in this earlier step. Keep in sync with the same-purpose
    helper in docs/index.html.

    ALSO protects meaningful compound words joined by a slash (e.g.
    "Hold/Resume", "On/Off") - these represent a single UI action/label, not
    two independent prose words, regardless of whether either half is a
    defined glossary term. Neither half may count as evidence of
    "inconsistent" casing, nor be individually recast by the mixed
    Title-case/sentence-case fix - found via MRG's report that "Hold/Resume"
    was being silently split apart (one half lowercased) by that fix."""
    phrases = sorted((k for k in (glossary or {}) if " " in k), key=len, reverse=True)
    spans = []

    def _add_span(start, end):
        if not any(s < end and start < e for s, e in spans):
            spans.append((start, end))

    if phrases:
        pattern = r"\b(?:" + "|".join(re.escape(p) for p in phrases) + r")\b"
        for m in re.finditer(pattern, v, flags=re.IGNORECASE):
            _add_span(m.start(), m.end())

    for m in _COMPOUND_WORD_RE.finditer(v):
        _add_span(m.start(), m.end())

    for m in _ACTION_REFERENCE_RE.finditer(v):
        _add_span(m.start(1), m.end(1))

    return spans


def _in_span(pos, spans):
    return any(s <= pos < e for s, e in spans)


def _fix_mixed_case_casing(v, glossary):
    """Fixing counterpart to check_entry()'s "Inconsistent capitalization"
    Grammar detection in language_rules.py - that function only DETECTS the
    mix (by design, per this project's detect/fix split), it never changes
    anything. Added 2026-08-27 per MRG's explicit request to also fix what
    gets flagged there. Uses the exact same gate as detection (content
    words >= 3, single sentence, a real capitalized/lowercase mix among the
    words after the first one, ignoring exempt words) so this never touches
    a string that detection wouldn't also flag.

    BUG FIX: this used to always assume sentence case was the "correct"
    target and blindly lowercase every capitalized content word after the
    first - so a string like "Please Hold/Resume the Process" with one
    genuinely inconsistent word elsewhere could still wrongly lowercase an
    unrelated, correctly-capitalized word like "Resume" just for being
    Title Case somewhere in the same string. Now the MAJORITY casing among
    the words actually eligible to change (i.e. excluding acronyms and
    protected terms - see `_is_exempt` below) is treated as this string's
    real style, and only the minority OUTLIER words get converted to match
    it; a word that's already consistent with the majority is never
    touched, regardless of its own case. A tie defaults to sentence case
    (this function's long-standing default direction).

    Also handles the separate case of a string written ENTIRELY in Title
    Case (every eligible word capitalized, no mix at all) - majority vote
    alone would call that "no problem" (100% agreement), but a normal UI
    sentence/message written throughout in Title Case is itself the issue
    MRG reported (e.g. "Oven Capacity Limit Exceeds the Maximum Number of
    Baking Activities Allowed in Parallel for Selected Oven"). When there's
    no lowercase word to vote against, sentence case is FORCED as the
    target rather than derived from the vote - see the `low_count` check
    below.

    A defined multi-word terminology phrase (e.g. "Order Number", "Handover
    Bin" - see `_protected_phrase_spans`) is excluded from the vote entirely
    and never recased here, same treatment as a single protected/exempt
    word - its casing is required, not a style choice, regardless of what
    the rest of the sentence does."""
    has_multiple_sentences = bool(re.search(r"[.!?]\s+[A-Z]", v))
    if has_multiple_sentences:
        return v
    v_for_case = re.sub(r"\bNo\.\s*", "", v)
    phrase_spans_for_case = _protected_phrase_spans(v_for_case, glossary)
    content_matches = [m for m in re.finditer(r"[A-Za-z']+", v_for_case)
                        if m.group(0).lower() not in _MINOR_WORDS_CASING and len(m.group(0)) > 1]
    if len(content_matches) < 3:
        return v
    rest_matches = content_matches[1:]

    from core.spellcheck import get_custom_words
    custom_words = get_custom_words()
    # Only single-word glossary entries here - multi-word phrases are
    # handled separately via `_protected_phrase_spans` above, since a phrase
    # is a defined TERM (protect every word in it, positionally), not a
    # single exempt token.
    protected = {k.lower() for k in (glossary or {}) if " " not in k} | \
        {str(x).lower() for k, x in (glossary or {}).items() if " " not in k}

    def _is_exempt(w):
        # ALL-CAPS acronym (e.g. "LAB") or a protected custom-dictionary/
        # glossary term (e.g. "Vegam", "DRL") - casing is deliberate and
        # never counts as evidence either way, nor is it ever changed.
        return w.isupper() or w.lower() in custom_words or w.lower() in protected

    votable = [m.group(0) for m in rest_matches
               if not _is_exempt(m.group(0)) and not _in_span(m.start(), phrase_spans_for_case)]
    cap_count = sum(1 for w in votable if w[:1].isupper() and not w.isupper())
    low_count = sum(1 for w in votable if w[:1].islower())
    if cap_count == 0:
        return v  # nothing capitalized among the words eligible to change - no fix needed

    target_is_title = (cap_count > low_count) if low_count else False

    seen_first_content_word = [False]
    phrase_spans_v = _protected_phrase_spans(v, glossary)

    def _recase(m):
        word = m.group(0)
        if word.lower() in _MINOR_WORDS_CASING or len(word) <= 1:
            return word
        if not seen_first_content_word[0]:
            seen_first_content_word[0] = True
            return word  # never touch the first content word
        if _in_span(m.start(), phrase_spans_v) or _is_exempt(word):
            return word  # defined multi-word term, or protected/acronym - casing is deliberate
        is_title = word[:1].isupper() and not word.isupper()
        if target_is_title and not is_title:
            return word[:1].upper() + word[1:]
        if not target_is_title and is_title:
            return word[:1].lower() + word[1:]
        return word  # already matches the winning style - leave it alone

    return re.sub(r"[A-Za-z']+", _recase, v)


def fix_value(value, glossary, use_language_tool=False, lang="en"):
    """Apply safe, deterministic fixes. Returns (fixed_value, [category, ...])."""
    cats = []
    v = value

    stripped = v.strip()
    if stripped != v:
        cats.append("Spacing (trim)")
        v = stripped

    # "No.Of" / "No. Of" -> "No. of" - real inconsistency found across this
    # team's actual files (same abbreviation, capitalized differently in
    # different strings). Fix before the general spacing rules run.
    fixed_noof = re.sub(r"No\.Of\b", "No. of", v)
    fixed_noof = re.sub(r"No\.\s*Of\b", "No. of", fixed_noof)
    if fixed_noof != v:
        cats.append("Grammar (No.Of casing)")
        v = fixed_noof

    # "No. of" -> "number of" - phrase-level consistency, "number of" is the
    # majority form actually used across this team's real file (verified:
    # 17 vs 8 occurrences), so that's the canonical direction.
    normalized_phrase = re.sub(r"\bNo\.\s*of\b", "number of", v, flags=re.IGNORECASE)
    if normalized_phrase != v:
        cats.append("Terminology (No. of -> number of)")
        v = normalized_phrase

    # Passive-voice noun-for-verb mixups (e.g. "been handovers" -> "been
    # handed over") - see PASSIVE_VOICE_FIXES above for why this can't be
    # caught by spelling (the noun form is a real dictionary word).
    for pattern, repl in PASSIVE_VOICE_FIXES.items():
        fixed_phrase = re.sub(pattern, repl, v, flags=re.IGNORECASE)
        if fixed_phrase != v:
            cats.append("Grammar (passive voice phrasing)")
            v = fixed_phrase

    # camelCase identifiers ending in "ID" (e.g. "formulaID", "materialID",
    # "barcodeID") - split BEFORE the glossary phrase-substitution block
    # below runs, so a defined canonical phrase (e.g. "barcode id" ->
    # "Barcode ID") still applies once the space exists. See
    # _split_id_identifiers above.
    id_split = _split_id_identifiers(v)
    if id_split != v:
        cats.append("Terminology (identifier + ID spacing)")
        v = id_split

    # Capitalized word right after a mid-sentence comma ("..., Please...")
    # should be lowercase - real, repeated pattern found in this team's file.
    # This runs AFTER the space-adding fix below (not before) so it also
    # catches "resource,Please" (no space at all) once that space exists.
    _CONTINUATION_WORDS = {
        "please", "process", "add", "check", "click", "select", "enter",
        "confirm", "contact", "ensure", "note", "review", "verify",
        "wait", "try", "refresh", "update", "save", "choose", "the",
        "this", "as", "and", "but",
    }
    def _decap(m):
        word = m.group(1)
        return f", {word.lower()}" if word.lower() in _CONTINUATION_WORDS else m.group(0)

    collapsed = re.sub(r" {2,}", " ", v)
    if collapsed != v:
        cats.append("Spacing (double space)")
        v = collapsed

    # add space after punctuation glued to the next word (run-on sentence),
    # but skip periods that precede a space already (e.g. " .gif" lists) or
    # form part of a file extension.
    spaced = re.sub(
        r"(?<!\s)([.,!?;:])(?=[A-Za-z])" + _EXT_GUARD, r"\1 ", v
    )
    if spaced != v:
        cats.append("Spacing (missing space after punctuation)")
        v = spaced

    decapped = re.sub(r",\s+([A-Z][a-z]+)\b", _decap, v)
    if decapped != v:
        cats.append("Grammar (comma-continuation casing)")
        v = decapped

    nospacebefore = re.sub(r"\s+([,!?;:])", r"\1", v)
    if nospacebefore != v:
        cats.append("Spacing (space before punctuation)")
        v = nospacebefore

    depunct = re.sub(r"([!?])\1+", r"\1", v)
    if depunct != v:
        cats.append("Punctuation (repeated !/?)")
        v = depunct

    deperiod = re.sub(r"\.\.(?!\.)", ".", v)
    if deperiod != v:
        cats.append("Punctuation (double period)")
        v = deperiod

    # Don't blindly uppercase a letter if the word starting right there is a
    # custom-dictionary term with deliberate internal casing (e.g. "iPAS",
    # "macOS") - doing so corrupts it (e.g. "iPAS" -> "IPAS"). Found via
    # MRG's explicit "leave iPAS as is" instruction, 2026-08-25.
    from core.spellcheck import get_mixed_case_custom_words
    mixed_case_words_for_cap = get_mixed_case_custom_words()

    # Capitalize the start of EVERY sentence, not just the first one in the
    # string. BUG FIX: this used to only ever touch v[0] - a lowercase
    # letter starting the second (or later) sentence of a multi-sentence
    # string (e.g. "Confirm the order. please proceed") was silently never
    # fixed. Only position 0 needs the "looks_like_sentence" gate (a short
    # 1-2 word label starting lowercase may be intentional); any position
    # right after an already-confirmed ". "/"! "/"? " boundary is
    # unambiguous on its own. The `cats` note is only added when the
    # ORIGINAL value actually had this issue somewhere, same discipline as
    # every other fix in this function.
    looks_like_sentence = len(v.split()) >= 3 or v.rstrip().endswith((".", "!", "?", ":"))

    def _cap_sentence_start(m):
        start = m.start(1)
        if start == 0 and not looks_like_sentence:
            return m.group(0)
        word_match = re.match(r"[A-Za-z][A-Za-z0-9']*", v[start:])
        word = word_match.group(0) if word_match else ""
        if word in mixed_case_words_for_cap:
            return m.group(0)
        return m.group(0)[:-1] + m.group(1).upper()

    capitalized = re.sub(r"(?:^|[.!?]\s+)([a-z])", _cap_sentence_start, v)
    if capitalized != v:
        v = capitalized
        cats.append("Grammar (capitalize sentence start)")

    # Mixed Title-Case/sentence-case fix - MRG's explicit choice (2026-08-27,
    # "Broader fix", after being shown a narrower alternative): once a
    # string already qualifies for the mixed-capitalization Grammar issue
    # (SAME gate as check_entry's detection - 3+ content words, single
    # sentence, a real cap/lowercase mix among the non-first content words),
    # lowercase every wrongly-capitalized content word after the first one,
    # treating sentence case as the target. Skips custom-dictionary and
    # glossary-protected words (IDH, Vegam, HCode, iPAS, DRL, etc.) so they
    # are never touched. Deliberately gated behind the exact same
    # conservative detection already proven safe for flagging - a short,
    # genuinely-intentional Title-Case label like "Please Confirm" never
    # reaches this code at all (only 2 content words, or no actual mix).
    mixed_case_fixed = _fix_mixed_case_casing(v, glossary)
    if mixed_case_fixed != v:
        cats.append("Grammar (inconsistent capitalization)")
        v = mixed_case_fixed

    if glossary:
        pattern = r"\b(" + "|".join(re.escape(k) for k in glossary) + r")\b"

        def repl(m):
            canon = glossary[m.group(0).lower()]
            return canon if m.group(0) != canon else m.group(0)

        new_v = re.sub(pattern, repl, v, flags=re.IGNORECASE)
        if new_v != v:
            cats.append("Terminology (abbreviation casing)")
            v = new_v

    if use_language_tool:
        tool = get_language_tool()
        if tool is not None:
            matches = tool.check(v)
            if matches:
                corrected = language_tool_python.utils.correct(v, matches)
                if corrected != v:
                    cats.append(f"LanguageTool ({len(matches)} suggestion(s))")
                    v = corrected

    from core.spellcheck import fix_spelling, get_custom_words
    # Glossary keys (e.g. "ccp") are known abbreviations, already handled by
    # the Terminology step above - skip them here too so spelling never
    # overwrites one with an unrelated dictionary guess (e.g. "ccp" -> "cp").
    spelling_skip_words = get_custom_words() | ({k.lower() for k in glossary} if glossary else set())
    spelled = fix_spelling(v, lang, spelling_skip_words)
    if spelled != v:
        cats.append("Spelling (dictionary correction)")
        v = spelled

    return v, cats


def detect_extra_issues(entries, fixed_values):
    """Detect issues that should NOT be auto-fixed, only flagged for review.
    Returns (duplicate_rows, other_flagged_rows) as two SEPARATE lists so
    duplicates can be reported on their own sheet/tab and never silently
    dropped or merged with other content."""
    seen_keys = {}
    duplicate_rows = []
    other_rows = []
    for e, fixed in zip(entries, fixed_values):
        key, lineno = e["key"], e["line"]
        if key in seen_keys:
            duplicate_rows.append({
                "line": lineno, "key": key,
                "value": e["value"],
                "first_seen_line": seen_keys[key],
                "note": "Duplicate key - both copies kept as-is, needs manual decision on which is correct",
            })
        else:
            seen_keys[key] = lineno

        # Detect against the ORIGINAL value, not `fixed` - this function's own
        # docstring says these are issues that should NOT be auto-fixed, only
        # flagged, so detection must follow the same rule as check_entry():
        # never let an earlier fix (spelling correction, casing, glossary
        # substitution, etc.) hide or fabricate a repeated-word match that
        # wasn't actually there in the source file being reviewed.
        words = e["value"].split()
        for i in range(len(words) - 1):
            if "[" in words[i] or "[" in words[i + 1]:
                continue
            w1 = re.sub(r"[^A-Za-z]", "", words[i]).lower()
            w2 = re.sub(r"[^A-Za-z]", "", words[i + 1]).lower()
            if w1 and len(w1) > 2 and w1 == w2:
                other_rows.append({
                    "line": lineno, "key": key,
                    "category": "Grammar",
                    "issue": f'Repeated word: "{words[i]} {words[i+1]}"',
                })
    return duplicate_rows, other_rows


def process_file(path, glossary, use_language_tool=False):
    """Full pipeline: parse -> fix -> detect extra issues.
    Returns (entries, diff_rows, duplicate_rows, other_flagged_rows)."""
    entries = parse_entries(path)
    diff_rows = []
    fixed_values = []

    for e in entries:
        new_value, cats = fix_value(e["value"], glossary, use_language_tool)
        fixed_values.append(new_value)
        if cats:
            diff_rows.append({
                "line": e["line"], "key": e["key"],
                "before": e["value"], "after": new_value,
                "categories": "; ".join(cats),
            })

    duplicate_rows, other_rows = detect_extra_issues(entries, fixed_values)
    return entries, diff_rows, duplicate_rows, other_rows


def write_fixed_file(src_path, out_path, glossary, use_language_tool=False):
    """Rewrite the whole file line-by-line, preserving comments/blank lines,
    only touching lines that match the resource-string pattern."""
    with open(src_path, encoding="utf-8") as f:
        lines = f.readlines()

    out_lines = []
    for raw in lines:
        m = LINE_PATTERN.match(raw.rstrip("\n"))
        if not m:
            out_lines.append(raw)
            continue
        key = m.group("key")
        new_value, _ = fix_value(m.group("value"), glossary, use_language_tool)
        semi = m.group("semi") or ""
        if not semi.strip():
            semi = ";"
        trail = m.group("trail") or ""
        out_lines.append(f'{m.group("indent")}languageResource.{key} = "{new_value}"{semi}{trail}\n')

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.writelines(out_lines)
