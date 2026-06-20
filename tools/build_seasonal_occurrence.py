#!/usr/bin/env python
"""季節フィルタの素データ（週次 × 種 の出現スコア表）を生成する。

出典 = BirdNET 同梱の eBird メタモデル（eBird 観測由来の出現確率予測器）。
これを録音地点の lat/lon で week 1..48 ぶん叩き、出現スコアを取得する。
カスタム CNN(models_Labels.txt, 165クラス) のラベルへ **学名(sci)** で突合して出力する
（英名の表記揺れを避けるため sci をキーにする）。

出力 data/seasonal_occurrence.csv 列:
  week_48, label(学名_英名), sci, ebird_score, in_ebird
    - ebird_score: その週の eBird 出現スコア（0..1, メタモデル出力）。in_ebird=False のとき 0。
    - in_ebird   : その種(sci)が eBird メタモデルのラベル集合に存在するか。
                   False の種は eBird データ無し → ランタイムで「常に在期」扱い（誤って落とさない）。

注意: これは eBird ベースの素データ。人手キュレーション（環境省/野鳥の会/現地知見）は
      data/seasonal_overrides.csv で別レイヤに足す（このファイルは再生成で上書きされる）。

使い方:
  .venv/bin/python tools/build_seasonal_occurrence.py [--lat L --lon LON] [--threshold 0.0]
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from birdnetlib.analyzer import Analyzer

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = BASE_DIR / "models" / "models_Labels.txt"
OUT_CSV = BASE_DIR / "data" / "seasonal_occurrence.csv"

# 録音地点（福岡市西区北原。settings.json の pi_devices と一致）
DEFAULT_LAT = 33.57869
DEFAULT_LON = 130.257151


def load_cnn_labels(labels_path: Path) -> dict[str, str]:
    """models_Labels.txt → {sci: "学名_英名" ラベル}。"""
    out: dict[str, str] = {}
    for line in labels_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or "_" not in line:
            continue
        sci = line.split("_", 1)[0].strip()
        out[sci] = line
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=DEFAULT_LAT)
    ap.add_argument("--lon", type=float, default=DEFAULT_LON)
    ap.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    ap.add_argument("--threshold", type=float, default=0.0,
                    help="メタモデル取得時の下限（0=全種のスコアを取る）。ハード閾値はランタイム側で適用。")
    ap.add_argument("--out", type=Path, default=OUT_CSV)
    args = ap.parse_args()

    cnn = load_cnn_labels(args.labels)
    print(f"CNN ラベル: {len(cnn)} 種 ({args.labels})")

    analyzer = Analyzer()  # メタモデル(species_class)をロード
    meta_sci = {lbl.split("_", 1)[0].strip() for lbl in analyzer.species_class.labels}
    no_ebird = sorted(s for s in cnn if s not in meta_sci)
    print(f"eBird メタモデル語彙: {len(meta_sci)} 種 / CNN種のうち eBird に無い: {len(no_ebird)}")
    if no_ebird:
        print("  (eBird データ無し→常に在期扱い):", ", ".join(no_ebird))

    rows = []
    for week in range(1, 49):
        species = analyzer.species_class.return_list(
            lon=args.lon, lat=args.lat, week_48=week, threshold=args.threshold)
        score_by_sci = {s["scientific_name"]: float(s["threshold"]) for s in species}
        for sci, label in cnn.items():
            in_ebird = sci in meta_sci
            score = score_by_sci.get(sci, 0.0) if in_ebird else 0.0
            rows.append((week, label, sci, round(score, 4), in_ebird))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["week_48", "label", "sci", "ebird_score", "in_ebird"])
        w.writerows(rows)
    print(f"書き出し: {args.out}  ({len(rows)} 行 = 48週 × {len(cnn)}種)")


if __name__ == "__main__":
    main()
