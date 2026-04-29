#!/bin/bash
rsync -az --remove-source-files \
  -e "ssh -i /home/mushipi/.ssh/id_ed25519_n97" \
  /home/mushipi/recordings/ \
  mushipi@100.102.9.77:/mnt/hamcam/BirdProject/raw_ingest/mushipi-bird01/
