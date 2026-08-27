import os
import csv
import uuid
from flask import Flask, render_template, request, send_from_directory, redirect, url_for, flash

from core.engine import load_glossary, process_file, write_fixed_file, _LT_AVAILABLE

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
GLOSSARY_PATH = os.path.join(BASE_DIR, "glossary.json")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = "dev-only-local-tool"  # fine for a local-only tool


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", lt_available=_LT_AVAILABLE)


@app.route("/run", methods=["POST"])
def run():
    file = request.files.get("resource_file")
    if not file or file.filename == "":
        flash("Please choose a .js resource file first.")
        return redirect(url_for("index"))

    use_lt = request.form.get("use_language_tool") == "on"

    run_id = uuid.uuid4().hex[:8]
    src_name = file.filename
    src_path = os.path.join(UPLOAD_DIR, f"{run_id}_{src_name}")
    file.save(src_path)

    glossary = load_glossary(GLOSSARY_PATH)
    entries, diff_rows, duplicate_rows, extra_rows = process_file(src_path, glossary, use_language_tool=use_lt)

    fixed_name = src_name.rsplit(".", 1)[0] + ".FIXED.js"
    fixed_path = os.path.join(OUTPUT_DIR, f"{run_id}_{fixed_name}")
    write_fixed_file(src_path, fixed_path, glossary, use_language_tool=use_lt)

    # Duplicates get their own standalone sheet - never merged into the main
    # report, never deleted from the fixed file, just called out separately.
    dup_name = src_name.rsplit(".", 1)[0] + ".duplicates.csv"
    dup_path = os.path.join(OUTPUT_DIR, f"{run_id}_{dup_name}")
    with open(dup_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["line", "key", "first_seen_line", "value", "note"])
        w.writeheader()
        for r in duplicate_rows:
            w.writerow(r)

    summary = {
        "total_strings": len(entries),
        "auto_fixed": len(diff_rows),
        "duplicates": len(duplicate_rows),
        "flagged_for_review": len(extra_rows),
    }

    return render_template(
        "report.html",
        summary=summary,
        diff_rows=diff_rows,
        duplicate_rows=duplicate_rows,
        extra_rows=extra_rows,
        run_id=run_id,
        fixed_filename=os.path.basename(fixed_path),
        dup_filename=os.path.basename(dup_path),
        src_filename=src_name,
    )


@app.route("/download/<path:filename>")
def download(filename):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    # Local tool only - do not expose this outside your machine.
    app.run(host="127.0.0.1", port=5001, debug=True)
