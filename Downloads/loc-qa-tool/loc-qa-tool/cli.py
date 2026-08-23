"""
CLI entry point - run the QA checker/fixer against one file or a whole folder
without opening the web UI. Useful for batch-processing all 40-50 files at once.

Usage:
    python cli.py path/to/file.js
    python cli.py path/to/folder_of_files/
    python cli.py path/to/folder_of_files/ --language-tool
"""
import argparse
import csv
import glob
import os

from core.engine import load_glossary, process_file, write_fixed_file

GLOSSARY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "glossary.json")


def run_one(path, glossary, out_dir, use_lt):
    entries, diff_rows, duplicate_rows, extra_rows = process_file(path, glossary, use_language_tool=use_lt)

    base = os.path.splitext(os.path.basename(path))[0]
    fixed_path = os.path.join(out_dir, f"{base}.FIXED.js")
    write_fixed_file(path, fixed_path, glossary, use_language_tool=use_lt)

    report_path = os.path.join(out_dir, f"{base}.diff_report.csv")
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["line", "key", "type", "categories_or_category", "before_or_issue", "after"])
        w.writeheader()
        for r in diff_rows:
            w.writerow({"line": r["line"], "key": r["key"], "type": "auto-fixed",
                        "categories_or_category": r["categories"], "before_or_issue": r["before"], "after": r["after"]})
        for r in extra_rows:
            w.writerow({"line": r["line"], "key": r["key"], "type": "needs-review",
                        "categories_or_category": r["category"], "before_or_issue": r["issue"], "after": ""})

    # Duplicates ALWAYS go on their own separate sheet - never merged into the
    # main report, never removed from the fixed file.
    dup_path = os.path.join(out_dir, f"{base}.duplicates.csv")
    with open(dup_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["line", "key", "first_seen_line", "value", "note"])
        w.writeheader()
        for r in duplicate_rows:
            w.writerow(r)

    print(f"{os.path.basename(path)}: {len(entries)} strings, {len(diff_rows)} auto-fixed, "
          f"{len(duplicate_rows)} duplicates, {len(extra_rows)} other flagged")
    print(f"  -> {fixed_path}")
    print(f"  -> {report_path}")
    print(f"  -> {dup_path}  (duplicates sheet)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="a .js resource file, or a folder containing several")
    ap.add_argument("--out", default="output", help="output folder (default: ./output)")
    ap.add_argument("--language-tool", action="store_true", help="also run LanguageTool spelling/grammar (requires it installed)")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    glossary = load_glossary(GLOSSARY_PATH)

    if os.path.isdir(args.path):
        files = sorted(glob.glob(os.path.join(args.path, "*.js")))
    else:
        files = [args.path]

    for f in files:
        run_one(f, glossary, args.out, args.language_tool)


if __name__ == "__main__":
    main()
