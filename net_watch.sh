#!/bin/bash
# One line a minute: are the two Tapo plugs reachable, and how strong is the
# Pi's own WiFi. Run from cron:
#
#   (crontab -l 2>/dev/null; echo "* * * * * bash /home/pi/Desktop/net_watch.sh") | crontab -
#
# The Pi's plug (192.168.1.100) shows offline in the Tapo app now and then while
# the PC's plug (192.168.1.103) never does. Both are P110M on the same router, so
# the difference is where they are: the Pi's plug sits inches from two 2.4 GHz
# Bluetooth radios that scan continuously. Logging both plugs together separates
# the cases -- .100 dropping alone is that plug or that spot, both dropping at
# once is the Pi's own WiFi or the AP.
#
#   grep DOWN /home/pi/net_watch.log | tail -30

LOG=/home/pi/net_watch.log
PLUGS="192.168.1.100 192.168.1.103"

out="$(date '+%F %T')"
for ip in $PLUGS; do
    if ping -c 1 -W 2 "$ip" >/dev/null 2>&1; then
        out="$out $ip=UP"
    else
        out="$out $ip=DOWN"
    fi
done

# The Pi is in the same place as its plug, with a better antenna. If this is
# weak the location has no coverage; if it is strong while .100 drops, the plug
# is being drowned out locally rather than being out of range.
sig=$(iw dev wlan0 link 2>/dev/null | awk '/signal:/ {print $2}')
out="$out wifi=${sig:-NA}dBm"

echo "$out" >> "$LOG"

# Keep it bounded -- this runs every minute forever and the SD card is small.
if [ "$(wc -l < "$LOG")" -gt 20000 ]; then
    tail -n 10000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
