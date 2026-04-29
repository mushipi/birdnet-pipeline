#!/bin/bash
# Pi 側セットアップスクリプト
# N97 から: scp -i ~/.ssh/id_ed25519_n97 BirdProject/pi-scripts/install_pi.sh mushipi@100.125.31.76:~/ && ssh mushipi@100.125.31.76 'bash install_pi.sh'

set -e

BIRDPROJECT_DIR="/home/mushipi/BirdProject"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== [1/5] BirdProject ディレクトリを作成 ==="
mkdir -p "$BIRDPROJECT_DIR"

echo "=== [2/5] record_config.sh を配置 ==="
# N97 の設定画面から上書きされるため、既存なら保持する
if [ ! -f "$BIRDPROJECT_DIR/record_config.sh" ]; then
    cp "$SCRIPT_DIR/record_config.sh" "$BIRDPROJECT_DIR/record_config.sh"
    echo "  record_config.sh を配置しました"
else
    echo "  既存の record_config.sh を保持します"
fi

echo "=== [3/5] record.sh と sync_to_n97.sh を配置 ==="
cp "$SCRIPT_DIR/record.sh" "$BIRDPROJECT_DIR/record.sh"
chmod +x "$BIRDPROJECT_DIR/record.sh"
cp "$SCRIPT_DIR/sync_to_n97.sh" "$BIRDPROJECT_DIR/sync_to_n97.sh"
chmod +x "$BIRDPROJECT_DIR/sync_to_n97.sh"

echo "=== [4/5] bird-record.service をインストール ==="
sudo cp "$SCRIPT_DIR/bird-record.service" /etc/systemd/system/bird-record.service
sudo systemctl daemon-reload
sudo systemctl enable bird-record.service
sudo systemctl restart bird-record.service

echo "=== [5/5] crontab に sync_to_n97.sh を登録（10分ごと）==="
# 既存の bird エントリを除去して再登録
(crontab -l 2>/dev/null | grep -v "sync_to_n97.sh"; echo "*/10 * * * * /bin/bash $BIRDPROJECT_DIR/sync_to_n97.sh >> /home/mushipi/sync.log 2>&1") | crontab -

echo ""
echo "=== セットアップ完了 ==="
echo ""
echo "【sudo NOPASSWD 設定が必要な場合】"
echo "  sudo visudo で以下を追加してください："
echo "  mushipi ALL=(ALL) NOPASSWD: /bin/systemctl restart bird-record.service"
echo ""
echo "【動作確認】"
echo "  sudo systemctl status bird-record.service"
echo "  crontab -l"
