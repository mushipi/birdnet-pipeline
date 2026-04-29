#!/bin/bash
# BirdNET ingest runner - raw_ingest_dir 配下のすべての pi サブディレクトリを処理する
set -euo pipefail

export PATH="/home/mushipi/.local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_FILE="/tmp/birdnet_ingest.lock"
LOG="$SCRIPT_DIR/ingest.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

if [ -f "$LOCK_FILE" ]; then
    log "既に実行中（ロックファイルあり）。スキップ。"
    exit 0
fi
touch "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

RAW_INGEST_DIR="$(python3 -c "import json; s=json.load(open('$SCRIPT_DIR/settings.json')); print(s['raw_ingest_dir'])")"
PROCESSED_DIR="$(python3 -c "import json; s=json.load(open('$SCRIPT_DIR/settings.json')); print(s['processed_dir'])")"

shopt -s nullglob
for pi_dir in "$RAW_INGEST_DIR"/*/; do
    pi_id="$(basename "$pi_dir")"
    log "Processing pi-id: $pi_id"
    cd "$SCRIPT_DIR"
    uv run python process.py --pi-id "$pi_id" >> "$LOG" 2>&1 || true
done

# noise WAV は DB 記録済みのため WAV 自体を削除してストレージを節約
find "$PROCESSED_DIR" -path "*/unknown/*.wav" -delete 2>/dev/null || true
log "Cleaned up noise WAV files."
