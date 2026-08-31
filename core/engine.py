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


def _fix_mixed_case_casing(v, glossary):
    """Fixing counterpart to check_entry()'s "Inconsistent capitalization"
    Grammar detection in language_rules.py - that function only DETECTS the
    mix (by design, per this project's detect/fix split), it never changes
    anything. Added 2026-08-27 per MRG's explicit request to also fix what
    gets flagged there. Uses the exact same gate as detection (content
    words >= 3, single sentence, a real capitalized/lowercase mix among the
    words after the first one) so this never touches a string that
    detection wouldn't also flag. Once gated in, treats sentence case as
    the target: every content word after the first is lowercased UNLESS
    it's protected (a custom-dictionary term or a glossary value/key -
    IDH, Vegam, HCode, iPAS, DRL, etc. must never be silently lowercased)."""
    has_multiple_sentences = bool(re.search(r"[.!?]\s+[A-Z]", v))
    if has_multiple_sentences:
        return v
    v_for_case = re.sub(r"\bNo\.\s*", "", v)
    content = [w for w in re.findall(r"[A-Za-z']+", v_for_case)
               if w.lower() not in _MINOR_WORDS_CASING and len(w) > 1]
    if len(content) < 3:
        return v
    rest = content[1:]
    has_cap = any(w[:1].isupper() and not w.isupper() for w in rest)
    has_lower = any(w[:1].islower() for w in rest)
    if not (has_cap and has_lower):
        return v

    from core.spellcheck import get_custom_words
    custom_words = get_custom_words()
    protected = {k.lower() for k in (glossary or {})} | {str(x).lower() for x in (glossary or {}).values()}

    seen_first_content_word = [False]

    def _decap(m):
        word = m.group(0)
        if word.lower() in _MINOR_WORDS_CASING or len(word) <= 1:
            return word
        if not seen_first_content_word[0]:
            seen_first_content_word[0] = True
            return word  # never touch the first content word
        if word.lower() in custom_words or word.lower() in protected:
            return word  # protected domain term - casing is deliberate
        if word[:1].isupper() and not word.isupper():
            return word[:1].lower() + word[1:]
        return word

    return re.sub(r"[A-Za-z']+", _decap, v)


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

    # Don't blindly uppercase the first letter if the first word is a
    # custom-dictionary term with deliberate internal casing (e.g. "iPAS",
    # "macOS") - doing so corrupts it (e.g. "iPAS" -> "IPAS"). Found via
    # MRG's explicit "leave iPAS as is" instruction, 2026-08-25.
    from core.spellcheck import get_mixed_case_custom_words
    first_word_match = re.match(r"[A-Za-z][A-Za-z0-9']*", v)
    first_word = first_word_match.group(0) if first_word_match else ""
    first_word_protected = first_word in get_mixed_case_custom_words()

    looks_like_sentence = len(v.split()) >= 3 or v.rstrip().endswith((".", "!", "?", ":"))
    if v and looks_like_sentence and v[0].islower() and v[0].isalpha() and not first_word_protected:
        v = v[0].upper() + v[1:]
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
    spelled = fix_spelling(v, lang, get_custom_words())
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
