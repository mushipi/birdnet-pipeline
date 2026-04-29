#!/bin/bash
# Pi Zero W 最適化版: CPU 優先度を下げ、圧縮なしで転送
nice -n 19 rsync -a --remove-source-files \
  -e "ssh -i /home/mushipi/.ssh/id_ed25519_n97" \
  /home/mushipi/recordings/ \
  mushipi@100.102.9.77:/mnt/hamcam/BirdProject/raw_ingest/mushipi-bird01/
