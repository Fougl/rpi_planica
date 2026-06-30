#!/usr/bin/env python3
# Live RSSI for the known cameras only, parsed from btmon.
#
# Usage (needs an active scan so the adverts flow):
#   Terminal 1:  bluetoothctl    then:  scan on
#   Terminal 2:  sudo stdbuf -oL btmon | python3 rssi_watch.py
#
import sys
import re
from datetime import datetime

# Same MACs as py_new.py, mapped to camera numbers.
KNOWN_CAMERAS = [
    'fb:9a:49:68:6b:f2', 'd5:ed:26:d6:c2:3b', 'dd:30:f0:c9:83:f0', 'e7:5c:2c:64:3c:1c',
    'ff:30:3a:eb:6b:d3', 'ef:be:79:67:78:46', 'f4:8f:f7:98:81:3a', 'f3:f6:b0:75:90:61',
    'ee:d5:4d:88:77:ff', 'ec:0c:e7:74:38:fc', 'ee:ea:a6:26:99:7e', 'e7:95:be:d1:c6:61',
    'cc:0b:1a:fd:8b:b6'
]
CAMERA_MAP = {mac: i + 1 for i, mac in enumerate(KNOWN_CAMERAS)}

addr_re = re.compile(r'Address:\s*([0-9A-Fa-f:]{17})')
rssi_re = re.compile(r'RSSI:\s*(-?\d+)\s*dBm')

cur = None
for line in sys.stdin:
    m = addr_re.search(line)
    if m:
        cur = m.group(1).lower()
        continue
    r = rssi_re.search(line)
    if r and cur in CAMERA_MAP:
        print("{}  Camera {:<2}  {}  RSSI {} dBm".format(
            datetime.now().strftime('%H:%M:%S'), CAMERA_MAP[cur], cur, r.group(1)))
        sys.stdout.flush()
        cur = None
