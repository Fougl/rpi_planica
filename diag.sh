#!/bin/bash
# Read-only diagnostics + a 60 s two-adapter RSSI census.
#
# Stops py_new for the census (~60 s) and restarts it. Output goes to the
# screen, to /home/pi/diag.txt, and is mailed to planica.zipline@gmail.com so
# it can be read off-site without screenshots.
#
#   cd /home/pi/Desktop && git pull && bash diag.sh

cd /home/pi/Desktop || exit 1
OUT=/home/pi/diag.txt
L=/home/pi/Desktop/new_log.log
[ -f "$L" ] || L=$(ls -t /home/pi/Desktop/*.log 2>/dev/null | head -1)

{
echo "=== $(date) on $(hostname) ==="
echo "== SERVICE =="
systemctl is-active py_new.service
systemctl show py_new.service -p ActiveEnterTimestamp --value
pgrep -af "py_new|bluepy-helper"
echo
echo "== ADAPTERS =="
python3 bt_adapters.py 2>&1 | tail -6
hciconfig | grep -E "^hci|BD Address|UP RUNNING|DOWN"
echo
echo "== LOG: $L ($(wc -l < "$L") lines) =="
for D in 2026-09-01 2026-09-02 2026-09-03 2026-09-04; do
  echo "--- $D ---"
  for p in "Script started" "returned STRONG" "returned WEAK" "trigger" \
           "START GATTTOOL" "SUCCESS FOR CAMERA" "Failed for" \
           "REMOVED - CAMERA" "Scan failed" "FAILED 5 TIMES"; do
    printf "  %-20s %s\n" "$p" "$(grep -c "^$D.*$p" "$L")"
  done
done
echo
echo "== absence/return events per camera, all time =="
grep -o "Camera [0-9]* ([^)]*) was absent" "$L" | sort | uniq -c | sort -rn
echo
echo "== every scanner state line on 09-03 / 09-04 (scanend spam removed) =="
grep -E "^2026-09-0[34].*(returned|trigger|REMOVED|Script started|Scan failed)" "$L" \
  | grep -v scanend | tail -60
} > "$OUT" 2>&1

echo "== CENSUS: stopping py_new for ~60 s ==" >> "$OUT"
sudo systemctl stop py_new.service
sleep 2
sudo python3 - >> "$OUT" 2>&1 <<'PY'
import sys
sys.path.insert(0, '/home/pi/Desktop')
import bt_adapters
from bluepy.btle import Scanner

KNOWN = ['fb:9a:49:68:6b:f2', 'd5:ed:26:d6:c2:3b', 'dd:30:f0:c9:83:f0', 'e7:5c:2c:64:3c:1c',
         'ff:30:3a:eb:6b:d3', 'ef:be:79:67:78:46', 'f4:8f:f7:98:81:3a', 'f3:f6:b0:75:90:61',
         'ee:d5:4d:88:77:ff', 'ec:0c:e7:74:38:fc', 'ee:ea:a6:26:99:7e', 'e7:95:be:d1:c6:61',
         'cc:0b:1a:fd:8b:b6', 'fc:8b:ba:54:66:d1']
CAM = {m: i + 1 for i, m in enumerate(KNOWN)}
S, G = bt_adapters.resolve()

# Same 30 s on each radio. The onboard one is the nearest thing we have to what
# the old Pi Zero heard; the difference between the two columns is the floor.
for label, hci in (("DONGLE  hci%d" % S, S), ("ONBOARD hci%d" % G, G)):
    print("\n== 30 s census on %s ==" % label)
    seen = {}
    try:
        sc = Scanner(hci)
        for _ in range(5):
            for d in sc.scan(6):
                m = d.addr.lower()
                if m in CAM:
                    seen.setdefault(m, []).append(d.rssi)
    except Exception as e:
        print("scan failed: %s" % e)
    print("%-4s %-18s %4s %5s %5s" % ("cam", "mac", "n", "best", "worst"))
    for m in KNOWN:
        v = seen.get(m, [])
        if v:
            print("%-4d %-18s %4d %5d %5d" % (CAM[m], m, len(v), max(v), min(v)))
        else:
            print("%-4d %-18s %4d %5s %5s  NOT SEEN" % (CAM[m], m, 0, "-", "-"))
PY
sudo systemctl start py_new.service
echo "== service after census: $(systemctl is-active py_new.service) ==" >> "$OUT"

cat "$OUT"

set -a; . /home/pi/Desktop/.env 2>/dev/null; set +a
python3 - <<'PY'
import os, smtplib
from email.message import EmailMessage
m = EmailMessage()
m["From"] = m["To"] = "planica.zipline@gmail.com"
m["Subject"] = "planica diag"
m.set_content(open("/home/pi/diag.txt", errors="replace").read())
try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login("planica.zipline@gmail.com", os.environ["SMTP_PASS"])
        s.send_message(m)
    print("\nMAILED diag.txt to planica.zipline@gmail.com")
except Exception as e:
    print("\nmail failed: %s" % e)
PY
