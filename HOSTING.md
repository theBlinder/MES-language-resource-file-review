# Hosting the static version for your team (GitHub Pages)

The `docs/index.html` file is a **fully self-contained, client-side** version
of the tool — same detection/fix rules as `core/engine.py`, reimplemented in
plain JavaScript. No server, no Python, nothing installed by your teammates.
Files never leave their browser (no upload happens anywhere).

## One-time setup (you, the repo owner)

```bash
git init
git add .
git commit -m "Initial localization QA tool"
git remote add origin <your-repo-url>
git push -u origin main
```

Then on GitHub: **Settings → Pages → Build and deployment → Source: Deploy
from a branch → Branch: main, folder: /docs → Save.**

GitHub gives you a URL like:
```
https://<your-username>.github.io/<repo-name>/
```

That's it — share that one link with your team.

## What your teammates do
1. Open the link.
2. Choose their `.js` file.
3. Click "Run QA check".
4. Review the report, download the fixed file and the duplicates sheet.

No install, no terminal, no Python — works the same in any modern browser.

## Keeping it in sync
If you update the glossary or fix rules, update **both**:
- `glossary.json` / `core/engine.py` (Python side — web UI + CLI)
- `docs/index.html` (`GLOSSARY` object + the matching JS functions)

They're intentionally two separate implementations (Python for local/batch
use, JS for the zero-install team site) — there's no shared code between them
to keep the static site truly dependency-free.

## Limitation
The LanguageTool (real spelling/grammar) integration only exists in the
Python version, since it needs a server to call. The static site covers
spacing, punctuation, capitalization, terminology/glossary, and duplicate-key
detection — not dictionary spell-checking.
