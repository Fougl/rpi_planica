# -*- coding: utf-8 -*-
# End-of-day sweep: send the gatttool command (same as py_new's run_gatttool) to
# every known camera at 18:00 Europe/Ljubljana.
#
# Cron fires this hourly; the script itself checks the Ljubljana hour and only
# acts at 18:00. That makes it correct regardless of the Pi's system timezone
# (the Pi's clock is on BST, Ljubljana is BST+1) — no system clock change needed.
import os
import sys
import time
import subprocess
import logging
from pathlib import Path

# Evaluate "now" in Ljubljana time regardless of the system timezone.
os.environ['TZ'] = 'Europe/Ljubljana'
time.tzset()

TARGET_HOUR = 18  # 18:00 Ljubljana

# Same cameras as py_new.py — keep this list in sync with that file.
KNOWN_CAMERAS = [
    'fb:9a:49:68:6b:f2', 'd5:ed:26:d6:c2:3b', 'dd:30:f0:c9:83:f0', 'e7:5c:2c:64:3c:1c',
    'ff:30:3a:eb:6b:d3', 'ef:be:79:67:78:46', 'f4:8f:f7:98:81:3a', 'f3:f6:b0:75:90:61',
    'ee:d5:4d:88:77:ff', 'ec:0c:e7:74:38:fc', 'ee:ea:a6:26:99:7e', 'e7:95:be:d1:c6:61',
    'cc:0b:1a:fd:8b:b6'
]
KNOWN_CAMERAS = [x.lower() for x in KNOWN_CAMERAS]
CAMERA_MAP = {mac: i + 1 for i, mac in enumerate(KNOWN_CAMERAS)}

log_file = Path("/home/pi/Desktop/new_log.log")
file_handler = logging.FileHandler(str(log_file), mode='a', encoding='utf-8')
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(console_handler)


def run_gatttool(mac):
    cam_num = CAMERA_MAP.get(mac, mac)
    logging.info(u"🌙 18:00 sweep — gatttool for Camera {} ({})".format(cam_num, mac))
    cmd = [
        "timeout", "--foreground", "3",
        "gatttool", "-t", "random", "-b", mac,
        "--char-write-req", "-a", "0x2f", "-n", "03170101"
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out_bytes, _ = proc.communicate()
        output = out_bytes.decode("utf-8", "ignore")
    except Exception as e:
        logging.error("Exception calling gatttool for {}: {}".format(mac, e))
        output = ""

    ok = "Characteristic value was written successfully" in output
    if ok:
        logging.info(u"🌙✅ Camera {} done".format(cam_num))
    else:
        logging.info("🌙 Camera {} no confirmation, output: {}".format(cam_num, output.strip()))

    try:
        subprocess.run(["bluetoothctl", "disconnect", mac],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=2, check=False)
    except Exception as e:
        logging.error("disconnect failed for {}: {}".format(mac, e))

    return ok


if __name__ == '__main__':
    force = len(sys.argv) > 1 and sys.argv[1] in ('--now', '--force')
    if not force and time.localtime().tm_hour != TARGET_HOUR:
        # Not 18:00 Ljubljana — cron fires hourly, so exit quietly otherwise.
        # Pass --now to run the sweep immediately (for testing).
        raise SystemExit(0)

    logging.info(u"🌙🌙🌙 18:00 Ljubljana — end-of-day gatttool sweep over all cameras 🌙🌙🌙")
    reached = 0
    missed = []
    for mac in KNOWN_CAMERAS:
        if run_gatttool(mac):
            reached += 1
        else:
            missed.append(CAMERA_MAP[mac])
        time.sleep(2)
    logging.info(u"🌙 Sweep complete: {}/{} reached. Missed cameras: {}".format(
        reached, len(KNOWN_CAMERAS), missed if missed else "none"))
