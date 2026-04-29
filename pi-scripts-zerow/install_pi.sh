#!/bin/bash
# Pi Zero W 用セットアップスクリプト
set -e

BIRDPROJECT_DIR="/home/mushipi/BirdProject"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== [1/5] BirdProject ディレクトリを作成 ==="
mkdir -p "$BIRDPROJECT_DIR"

echo "=== [2/5] record_config.sh を配置 ==="
if [ ! -f "$BIRDPROJECT_DIR/record_config.sh" ]; then
    # デフォルト設定を作成
    echo "RECORD_START=4" > "$BIRDPROJECT_DIR/record_config.sh"
    echo "RECORD_STOP=22" >> "$BIRDPROJECT_DIR/record_config.sh"
    echo "RECORD_ALL_DAY=0" >> "$BIRDPROJECT_DIR/record_config.sh"
    echo "SEGMENT_SEC=60" >> "$BIRDPROJECT_DIR/record_config.sh"
    echo "  record_config.sh を新規作成しました"
else
    echo "  既存の record_config.sh を保持します"
fi

echo "=== [3/5] Zero W 最適化スクリプトを配置 ==="
cp "$SCRIPT_DIR/record.sh" "$BIRDPROJECT_DIR/record.sh"
chmod +x "$BIRDPROJECT_DIR/record.sh"
cp "$SCRIPT_DIR/sync_to_n97.sh" "$BIRDPROJECT_DIR/sync_to_n97.sh"
chmod +x "$BIRDPROJECT_DIR/sync_to_n97.sh"

echo "=== [4/5] bird-record.service をインストール ==="
# service ファイルは共通のものを使用
if [ -f "$SCRIPT_DIR/../pi-scripts/bird-record.service" ]; then
    sudo cp "$SCRIPT_DIR/../pi-scripts/bird-record.service" /etc/systemd/system/bird-record.service
    sudo systemctl daemon-reload
    sudo systemctl enable bird-record.service
    sudo systemctl restart bird-record.service
fi

echo "=== [5/5] crontab に登録（10分ごと）==="
(crontab -l 2>/dev/null | grep -v "sync_to_n97.sh"; echo "*/10 * * * * /bin/bash $BIRDPROJECT_DIR/sync_to_n97.sh >> /home/mushipi/sync.log 2>&1") | crontab -

echo ""
echo "=== セットアップ完了 (Zero W 最適化版) ==="
