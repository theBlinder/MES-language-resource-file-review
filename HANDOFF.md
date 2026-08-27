# Localization QA Tool - Handoff

## What this is
A tool that checks localization/language resource files (`languageResource.KEY
= "value";` style `.js` files) for spelling, spacing, grammar, terminology
consistency, and duplicate keys - and produces a single Excel workbook with
one sheet per issue type, plus a "Clean" sheet for strings with no issues.

Built for internal use, starting with one team, with the goal of becoming a
company-wide standard tool once validated.

## Current status
**Working, tested, not yet publicly deployed.** Two versions exist, built from
the same rules, kept in sync manually:

| | Python (local/CLI) | Static site (`docs/`) |
|---|---|---|
| Where it runs | Your machine, via terminal | Any browser, once hosted |
| Spelling engine | Hunspell via system library (ctypes) | Hunspell-format dictionary bundled in `docs/dictionaries/`, read via `typo-js` |
| Best for | One person, batch-processing many files at once | Sharing with the whole team via one URL |
| Setup needed | Python + `pip install -r requirements.txt` + OS hunspell package | None - just open the URL |

Both were validated against a real 1,257-line file from this team and produce
matching counts (spacing, grammar, terminology, duplicates all confirmed
identical between the two implementations).

## What's NOT done yet
- **Not deployed to a live URL yet.** The static site works locally but
  hasn't been pushed to GitHub / GitHub Pages turned on. See "Next steps."
- **Only English spelling is bundled.** German/French/Spanish/etc. dictionaries
  need to be added (small effort per language - see "Adding a language" below).
- **Chinese and Japanese have no spelling support at all.** Hunspell doesn't
  fit these languages (no word-boundary concept the way English has). Real
  coverage needs a different tool (LanguageTool, self-hosted) - not built yet.
- **No real grammar checking** (only structural pattern checks: repeated
  punctuation, capitalization, missing spaces). Deeper grammar needs
  LanguageTool too - wired as an optional hook in the Python version
  (`core/engine.py`) but not connected to any running server.
- **Spelling suggestions are not always correct** - review before applying,
  never bulk-replace blindly (e.g. "sarch" suggested "arch" over "search").

## Repository layout
```
loc-qa-tool/
  docs/index.html          <- the static site (THIS is what GitHub Pages serves)
  docs/dictionaries/         <- bundled Hunspell dictionary files (English only so far)
  app.py                      <- Flask local web UI (alternative to docs/, needs Python)
  cli.py / cli_excel.py         <- command-line batch runners
  core/engine.py                 <- Python: parsing + fix rules
  core/language_rules.py          <- Python: per-category, language-aware detection
  core/excel_report.py             <- Python: builds the .xlsx workbook
  core/spellcheck.py                <- Python: Hunspell spelling via ctypes
  glossary.json                      <- abbreviation/terminology list (e.g. ip -> IP)
  custom_dictionary.txt                <- domain words that aren't typos (e.g. "preweigh")
  README.md, HOSTING.md                 <- setup and GitHub Pages instructions
```

## Step-by-step: what to do right now
1. Unzip the `loc-qa-tool.zip` you were given.
2. If you already made a GitHub repo but haven't pushed: open a terminal in
   the unzipped folder and run, one at a time:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin <your-repo-url>.git
   git branch -M main
   git push -u origin main
   ```
   (If you'd already done some of this, `git add .` + `git commit -m "..."` +
   `git push` is enough to pick up from where you left off - git will only
   push what changed.)
3. On GitHub: **Settings -> Pages -> Source: Deploy from a branch -> Branch:
   main -> Folder: /docs -> Save.**
4. Wait 1-2 minutes, refresh that page, copy the URL GitHub shows you
   (`https://<username>.github.io/<repo>/`).
5. Open that URL yourself, upload a real `.js` file, run the check, download
   the Excel file, and open it - confirm the Spelling sheet actually catches
   real typos (this was broken before this handoff and should be fixed now,
   but verify on real infrastructure since it couldn't be tested in a
   sandboxed environment without internet access).
6. If it works, send the URL to your team.

## Adding a language (e.g. German)
1. On a machine with internet: `sudo apt-get install hunspell-de-de` (Linux)
   or get `de_DE.aff`/`de_DE.dic` some other way.
2. Copy those two files into `docs/dictionaries/`.
3. In `docs/index.html`, add a line to `SPELLCHECK_DICTS`:
   ```js
   de: { code: "de_DE", aff: "dictionaries/de_DE.aff", dic: "dictionaries/de_DE.dic" },
   ```
4. Commit and push - live site picks it up automatically.

## Who to loop in
- **A developer**, to confirm the real filename/language-code convention used
  across your actual 400-500 files (right now language is guessed from the
  filename, e.g. `_fr.js`, with a manual dropdown fallback).
- **IT/infra**, if/when you want real grammar checking or Chinese/Japanese
  support - that requires standing up a self-hosted LanguageTool server on
  the company network (already agreed as acceptable, not yet built).
