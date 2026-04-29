#!/bin/bash
source /home/mushipi/BirdProject/record_config.sh
mkdir -p /home/mushipi/recordings

while true; do
    hour=$(date +%-H)
    if [ "${RECORD_ALL_DAY:-0}" = "1" ] || ([ "$hour" -ge "$RECORD_START" ] && [ "$hour" -lt "$RECORD_STOP" ]); then
        fname="/home/mushipi/recordings/$(date '+%Y%m%d_%H%M%S').wav"
        timeout "$SEGMENT_SEC" arecord -D hw:0,0 -f S16_LE -r 48000 -c 1 "$fname"
    else
        sleep 60
    fi
done
