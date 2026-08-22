"""
Generates the multi-sheet Excel QA report for one file or a whole folder.

Usage:
    python cli_excel.py path/to/file.js
    python cli_excel.py path/to/folder_of_files/ --out output/
    python cli_excel.py path/to/resource_fr.js --lang fr   # manual override

Language is auto-detected from the filename when possible (e.g. "_fr.js").
If it can't be detected, the report still runs using safe/English-like
defaults, but the Summary sheet will say "Unknown" so you know to check it
manually rather than trusting a silent guess.

To enable real spelling/grammar checking, point this at a self-hosted
LanguageTool server (see README) - without one, the Spelling sheet stays
empty and the Summary sheet says so explicitly.
"""
import argparse
import glob
import os

from core.excel_report import build_report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="a .js resource file, or a folder containing several")
    ap.add_argument("--out", default="output", help="output folder (default: ./output)")
    ap.add_argument("--lang", default=None, help="manual language code override (e.g. fr, de, ja) - applies to ALL files in this run")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    if os.path.isdir(args.path):
        files = sorted(glob.glob(os.path.join(args.path, "*.js")))
    else:
        files = [args.path]

    for f in files:
        base = os.path.splitext(os.path.basename(f))[0]
        out_path = os.path.join(args.out, f"{base}.qa_report.xlsx")
        stats = build_report(f, out_path, lang_override=args.lang, lt_client=None)
        print(f"{os.path.basename(f)}  [{stats['language']}, detected via {stats['detection']}]")
        print(f"  {stats['total']} strings | spelling {stats['spelling']} | spacing {stats['spacing']} | "
              f"grammar {stats['grammar']} | terminology {stats['terminology']} | "
              f"duplicates {stats['duplicates']} | clean {stats['clean']}")
        print(f"  -> {out_path}")


if __name__ == "__main__":
    main()
