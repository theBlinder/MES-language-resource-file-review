# Localization QA Tool

A local, team-internal tool to check and auto-fix `languageResource.KEY = "value";`
style JS localization resource files for:

- **Spacing** — double spaces, missing space after punctuation, stray whitespace
- **Punctuation** — repeated `!!`/`??`, stray double periods
- **Grammar** — sentence not capitalized (safe cases only)
- **Terminology** — abbreviation casing via a glossary (e.g. `ip` → `IP`)
- **Structure** — duplicate keys (flagged, not auto-fixed — needs a human call)
- *(optional)* real spelling/grammar via [LanguageTool](https://languagetool.org/), if installed

Two ways to run it: a local web UI (`app.py`) or a CLI for batch runs across
all your files at once (`cli.py`).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run the web UI

```bash
python app.py
```
Then open http://127.0.0.1:5001 — upload a `.js` file, review the report,
download the fixed copy. Your original file is never modified.

## Run from the command line (batch mode)

```bash
# one file
python cli.py path/to/ManufactureCalenderScriptResource_v2.js

# a whole folder of resource files at once
python cli.py path/to/all_resource_files/ --out output/
```

## Adding real spelling/grammar checking (LanguageTool)

This needs internet + Java the first time you set it up (not needed to run
this sandbox demo, but fine on your work machine):

```bash
pip install language-tool-python
```
Then check the "Use LanguageTool" box in the web UI, or pass `--language-tool`
to the CLI. The first run downloads a local LanguageTool server automatically.

## Extending the glossary

Edit `glossary.json` — add any abbreviation your product uses:
```json
{
  "ip": "IP",
  "sop": "SOP",
  "your_abbr": "YOUR_CANONICAL_FORM"
}
```
No code changes needed.

## Project layout

```
loc-qa-tool/
  app.py            # Flask web UI
  cli.py             # batch/command-line runner
  core/engine.py      # parsing, detection, and fix rules (single source of truth)
  glossary.json        # team's abbreviation/terminology list
  templates/            # HTML for the web UI
  uploads/  output/       # runtime folders (gitignored)
```

## Publishing to your git

```bash
git init
git add .
git commit -m "Initial localization QA tool"
git remote add origin <your-repo-url>
git push -u origin main
```

## Notes / next steps
- Duplicate keys are intentionally **not** auto-fixed — pick the correct
  string manually, since the tool can't know which one is right.
- Auto-fix rules are conservative on purpose (e.g. periods followed by a
  space are left alone, to avoid mangling file-extension lists like
  `.gif or .png`). If you hit a rule that's too aggressive or not aggressive
  enough for your files, tune it in `core/engine.py::fix_value`.
- Scale to all 40-50 files with `python cli.py path/to/folder/`.
