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


def fix_value(value, glossary, use_language_tool=False):
    """Apply safe, deterministic fixes. Returns (fixed_value, [category, ...])."""
    cats = []
    v = value

    stripped = v.strip()
    if stripped != v:
        cats.append("Spacing (trim)")
        v = stripped

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

    looks_like_sentence = len(v.split()) >= 3 or v.rstrip().endswith((".", "!", "?", ":"))
    if v and looks_like_sentence and v[0].islower() and v[0].isalpha():
        v = v[0].upper() + v[1:]
        cats.append("Grammar (capitalize sentence start)")

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

        words = fixed.split()
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
