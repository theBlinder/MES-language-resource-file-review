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

## Adding real spelling checking (Hunspell, works offline once installed)

Spelling uses Hunspell (same engine as LibreOffice/Firefox), via system dictionaries -
no internet needed at runtime, just an OS package installed once:

```bash
# Debian/Ubuntu
sudo apt-get install hunspell-en-us      # or hunspell-en-gb for British English
sudo apt-get install hunspell-de-de      # German
sudo apt-get install hunspell-fr         # French
# etc - search "hunspell-<lang>" for your language

# macOS (Homebrew)
brew install hunspell
# then download the .dic/.aff for your language into ~/Library/Spelling/
```

**Important: pick en-US or en-GB and stay consistent.** "cancelled" (British) vs
"canceled" (American) will get flagged as wrong if your dictionary doesn't match
your product's spelling convention - check the Spelling sheet for this pattern
if you see unexpected results.

**Chinese and Japanese are not well served by Hunspell** - these languages
don't have "misspelled words" in the same word-boundary sense English does.
For real quality on CJK languages, you'll want the self-hosted LanguageTool
route (`language-tool-python`, see below) instead, which has dedicated
support for many more languages including Japanese and (more limited) Chinese.

### Domain-specific words (custom_dictionary.txt)

Product/technical terms that aren't real dictionary words but ARE correct
for your product (e.g. "preweigh", "packoff") will get flagged as typos
unless you add them to `custom_dictionary.txt` (one word per line). Do this
whenever the Spelling sheet surfaces a false positive that's actually your
terminology, not a mistake.

**The top suggestion is not always correct** - e.g. "sarch" suggests "arch"
before "search". Always treat the Suggested Change column as something to
review, never something to blindly find-and-replace with.

## Adding LanguageTool (deeper grammar + broader language coverage)

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
