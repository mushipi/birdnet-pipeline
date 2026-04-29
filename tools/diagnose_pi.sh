#!/bin/bash
# Pi 接続診断スクリプト - 問題の層を特定して修復手順を案内する

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS="$SCRIPT_DIR/../settings.json"

PI_HOST=$(python3 -c "import json; print(json.load(open('$SETTINGS'))['pi_host'])")
PI_TAILSCALE_IP=$(echo "$PI_HOST" | grep -oP '\d+\.\d+\.\d+\.\d+')
PI_LAN_IP="192.168.31.x"  # 必要なら settings.json に追加

OK="[OK]"
NG="[NG]"
SKIP="[--]"

echo "============================================"
echo "  BirdProject Pi 接続診断"
echo "  対象: $PI_HOST"
echo "  実行: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================"
echo ""

# --- Layer 1: Tailscale ---
echo "[ Layer 1 ] Tailscale 接続"
if command -v tailscale &>/dev/null; then
    TS_STATUS=$(tailscale status 2>/dev/null | grep "$PI_TAILSCALE_IP" | head -1 || true)
    if [ -z "$TS_STATUS" ]; then
        echo "  $NG Pi の Tailscale IP ($PI_TAILSCALE_IP) がステータス一覧に見当たらない"
        echo ""
        echo "  >>> 対処: Pi の電源・Tailscale デーモンを確認"
        echo "       Pi 側: sudo systemctl status tailscaled"
        echo "       Pi 側: sudo tailscale up"
        echo ""
        echo "診断終了: Tailscale 層で断絶。以降の層は確認不可。"
        exit 1
    fi
    if echo "$TS_STATUS" | grep -q "offline"; then
        echo "  $NG Pi は Tailscale に登録されているが offline"
        LAST_SEEN=$(echo "$TS_STATUS" | grep -oP 'last seen \K[^,]+' || echo "不明")
        echo "  最終オンライン: $LAST_SEEN"
        echo ""
        echo "  >>> 対処: Pi を再起動するか、Tailscale サービスを確認"
        echo "       Pi 側: sudo reboot"
        echo "       または: sudo systemctl restart tailscaled && sudo tailscale up"
        echo ""
        echo "診断終了: Tailscale は offline。Pi に物理アクセスが必要な可能性あり。"
        exit 1
    fi
    echo "  $OK Pi は Tailscale で online"
else
    echo "  $SKIP tailscale コマンドなし（スキップ）"
fi

# --- Layer 2: ICMP ping (Tailscale IP) ---
echo ""
echo "[ Layer 2 ] ICMP ping ($PI_TAILSCALE_IP)"
if ping -c 3 -W 2 "$PI_TAILSCALE_IP" &>/dev/null; then
    echo "  $OK ping 応答あり"
else
    echo "  $NG ping タイムアウト ($PI_TAILSCALE_IP)"
    echo ""
    echo "  >>> 対処: Tailscale VPN のルーティングを確認"
    echo "       N97 側: sudo tailscale ping $PI_TAILSCALE_IP"
    echo ""
    echo "診断終了: ICMP 層で断絶。"
    exit 1
fi

# --- Layer 3: SSH ---
echo ""
echo "[ Layer 3 ] SSH 接続 ($PI_HOST)"
SSH_OPTS="-q -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no"
if ssh $SSH_OPTS "$PI_HOST" "exit 0" 2>/dev/null; then
    echo "  $OK SSH 接続成功"
else
    echo "  $NG SSH 接続失敗"
    echo ""
    echo "  >>> 対処:"
    echo "       1. Pi 側 SSH サービス確認: sudo systemctl status ssh"
    echo "       2. 鍵認証確認: ssh-copy-id $PI_HOST"
    echo "       3. known_hosts クリア: ssh-keygen -R $PI_TAILSCALE_IP"
    echo ""
    echo "診断終了: SSH 層で断絶。"
    exit 1
fi

# --- Layer 4: アプリケーション層 ---
echo ""
echo "[ Layer 4 ] Pi 側アプリケーション確認"

# bird-record.service
REC_STATUS=$(ssh $SSH_OPTS "$PI_HOST" "systemctl is-active bird-record.service 2>/dev/null || echo 'unknown'")
if [ "$REC_STATUS" = "active" ]; then
    echo "  $OK bird-record.service: active"
else
    echo "  $NG bird-record.service: $REC_STATUS"
    echo "       N97 側から再起動: ssh $PI_HOST 'sudo systemctl restart bird-record.service'"
fi

# sync_to_n97.sh の最終実行時刻
SYNC_LOG=$(ssh $SSH_OPTS "$PI_HOST" "tail -3 /home/mushipi/sync.log 2>/dev/null || echo '(sync.log なし)'")
echo "  sync.log (最終3行):"
echo "$SYNC_LOG" | sed 's/^/    /'

# recordings/ のファイル数
REC_COUNT=$(ssh $SSH_OPTS "$PI_HOST" "ls /home/mushipi/recordings/*.wav 2>/dev/null | wc -l || echo 0")
echo "  Pi recordings/ の未転送ファイル数: $REC_COUNT"
if [ "$REC_COUNT" -gt 20 ]; then
    echo "  $NG 未転送ファイルが $REC_COUNT 件溜まっている（rsync が滞留中の可能性）"
    echo "       手動 rsync: ssh $PI_HOST 'bash /home/mushipi/BirdProject/sync_to_n97.sh'"
fi

# N97 side: raw_ingest に WAV があるか
INGEST_COUNT=$(find /mnt/hamcam/BirdProject/raw_ingest/mushipi-bird01/ -name '*.wav' 2>/dev/null | wc -l)
echo "  N97 raw_ingest 内の未処理ファイル数: $INGEST_COUNT"

echo ""
echo "============================================"
echo "  診断完了: 全層 OK"
echo "  問題なし。次回 ingest timer (10分) で処理されます。"
echo "============================================"
