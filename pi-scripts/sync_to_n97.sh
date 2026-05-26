#!/bin/bash
# N97_TAILSCALE_IP を環境変数またはスクリプト引数で指定してください
# 例: export N97_TAILSCALE_IP=100.x.x.x
N97_TAILSCALE_IP="${N97_TAILSCALE_IP:?N97_TAILSCALE_IP is not set}"

rsync -az --remove-source-files \
  -e "ssh -i /home/mushipi/.ssh/id_ed25519_n97" \
  /home/mushipi/recordings/ \
  mushipi@${N97_TAILSCALE_IP}:/mnt/hamcam/BirdProject/raw_ingest/mushipi-bird01/