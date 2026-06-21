#!/bin/bash
# 03_train 直後の必須後処理ゲート（sci統一スキップ事故の恒久対策）。
# 失敗したら exit≠0 で止まり、配備に進ませない。
set -euo pipefail
cd /home/mushipi/Scripts/BirdProject
PY=./.venv/bin/python
MASTER=/home/mushipi/Scripts/bird-fine-classifier/data/species_master.csv

echo "[finalize] 1/2 sci統一(relabel, master駆動・fail-loud)"
$PY scripts/relabel_labels_sci.py --master "$MASTER" --write-model-label

echo "[finalize] 2/2 契約lint(retrainゲート)"
$PY scripts/validate_species_contract.py \
    --labels models_Labels.txt --master "$MASTER" --raw-dir data/raw

echo "[finalize] OK = 配備可。配備先(GT105)でも rsync 後に同 lint を --seasonal 込みで実行すること。"
