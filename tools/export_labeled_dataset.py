"""
人手検証ラベル（human_label='correct'/'wrong'）が付いた検出を、カスタム分類器学習用に
ZIP に出力する。Phase 5-D（カスタム分類器）の入力データ。

ZIP 構造:
    correct/<species>/<id>_<filename>.wav
    wrong/<species>/<id>_<filename>.wav
    metadata.csv  (id, species, scientific_name, confidence, human_label, file_path)
"""

import csv
import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS = json.loads((BASE_DIR / "settings.json").read_text(encoding="utf-8"))
DB_PATH = SETTINGS["db_path"]


def main(output_path: str = "labeled_dataset.zip"):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, species, scientific_name, confidence, human_label, file_path "
        "FROM detections WHERE human_label IN ('correct','wrong') ORDER BY species, id"
    ).fetchall()
    conn.close()

    if not rows:
        print("[INFO] human_label が付いた検出がありません。")
        return

    out = Path(output_path)
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerow(["id", "species", "scientific_name", "confidence", "human_label", "file_path"])

    added, missing = 0, 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zf:
        for r in rows:
            writer.writerow([r["id"], r["species"], r["scientific_name"],
                             f"{r['confidence']:.3f}" if r["confidence"] else "",
                             r["human_label"], r["file_path"]])
            wav = Path(r["file_path"])
            if not wav.exists():
                missing += 1
                continue
            sci = (r["scientific_name"] or "unknown").replace(" ", "_")
            arcname = f"{r['human_label']}/{sci}/{r['id']}_{wav.name}"
            zf.write(wav, arcname)
            added += 1
        zf.writestr("metadata.csv", csv_buf.getvalue())

    print(f"[OK] {added} files exported to {out} (missing WAV: {missing})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "labeled_dataset.zip")
