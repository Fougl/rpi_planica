# -*- coding: utf-8 -*-
# End-of-day sweep: run gatttool on cameras 6-13 at 18:00 Europe/Ljubljana,
# using the EXACT same gatttool logic as py_new.py (run_gatttool, the retry loop
# and send_failure_email are copied verbatim). py_new stays running — its bluepy
# scan coexists with gatttool exactly as it does during normal operation.
#
# Cron fires this hourly; the script checks the Ljubljana hour and only acts at
# 18:00, so it is correct regardless of the Pi's system timezone.
import os
import sys
import time
import subprocess
import logging
from pathlib import Path
import smtplib
from email.message import EmailMessage
from datetime import datetime

import bt_adapters

# Writes go out on the GATT adapter (the onboard radio), leaving the dongle free
# to keep scanning for py_new. Python puts this script's own directory on
# sys.path, so the import works from root's cron with any working directory.
_SCAN_HCI, GATT_HCI = bt_adapters.resolve()

# Evaluate "now" in Ljubljana time regardless of the system timezone.
os.environ['TZ'] = 'Europe/Ljubljana'
time.tzset()

TARGET_HOUR = 18  # 18:00 Ljubljana

# === Email Configuration ===
EMAIL_FROM = "planica.zipline@gmail.com"
EMAIL_TO = "planica.zipline@gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_PASS = os.environ["SMTP_PASS"]

# === Known Camera MACs (same as py_new.py) ===
KNOWN_CAMERAS = [
    'fb:9a:49:68:6b:f2', 'd5:ed:26:d6:c2:3b', 'dd:30:f0:c9:83:f0', 'e7:5c:2c:64:3c:1c',
    'ff:30:3a:eb:6b:d3', 'ef:be:79:67:78:46', 'f4:8f:f7:98:81:3a', 'f3:f6:b0:75:90:61',
    'ee:d5:4d:88:77:ff', 'ec:0c:e7:74:38:fc', 'ee:ea:a6:26:99:7e', 'e7:95:be:d1:c6:61',
    'cc:0b:1a:fd:8b:b6',
    # cam14 -- had a setup.sh alias but was never in this list, so the
    # scanner ignored it and only the manual cam14on alias could wake it.
    'fc:8b:ba:54:66:d1',
]
KNOWN_CAMERAS = [x.lower() for x in KNOWN_CAMERAS]
CAMERA_MAP = {mac: i + 1 for i, mac in enumerate(KNOWN_CAMERAS)}

# === Logging ===
log_file = Path("/home/pi/Desktop/new_log.log")
file_handler = logging.FileHandler(str(log_file), mode='a', encoding='utf-8')
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(console_handler)

# === Email Failure Alert (verbatim from py_new.py) ===
def send_failure_email(camera_number):
    msg = EmailMessage()
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = "GATT write failed 5 times for Camera {}".format(camera_number)
    msg.set_content(
        "All 5 attempts to write to camera {} failed at {}.".format(
            camera_number,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
    )

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(EMAIL_FROM, SMTP_PASS)
            server.send_message(msg)
        logging.info("Sent failure email for camera {}".format(camera_number))
    except Exception as e:
        logging.error("Failed to send email for camera {}: {}".format(camera_number, e))

# === GATT Execution (verbatim from py_new.py) ===
def run_gatttool(mac, macs_to_process, attempt_counter):
    cam_num = CAMERA_MAP.get(mac, mac)
    if attempt_counter[mac]==1:
        logging.info(u"🚀🚀🚀🚀🚀 START GATTTOOL FOR CAMERA {} ({}) 🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀".format(cam_num, mac))
    logging.info("Running gatttool for Camera {} ({})".format(cam_num, mac))

    cmd = [
        "timeout", "--foreground", "10",
        "gatttool", "-i", "hci{}".format(GATT_HCI),
        "-t", "random", "-b", mac,
        "--char-write-req", "-a", "0x2f", "-n", "03170101"
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out_bytes, _ = proc.communicate()
        output = out_bytes.decode("utf-8", "ignore")
    except Exception as e:
        logging.error("Exception calling gatttool for {}: {}".format(mac, e))
        output = ""

    success = "Characteristic value was written successfully" in output
    if success:
        logging.info(u"🥇🥇🥇🥇🥇🥇SUCCESS FOR CAMERA{} 🥇🥇🥇🥇🥇🥇🥇🥇🥇🥇🥇🥇".format(cam_num))
        attempt_counter[mac] = 0
        if mac in macs_to_process:
            del macs_to_process[mac]
    else:
        logging.info("Failed for {}, output: {}".format(mac, output.strip()))

    # NOTE: no bluetoothctl disconnect / bluetooth restart here (unlike py_new).
    # gatttool drops its own raw connection when it exits, so there is nothing for
    # bluetoothctl to disconnect — that call always timed out (bluetoothd never saw
    # gatttool's connection) and its handler ran `systemctl restart bluetooth` on
    # EVERY camera. In this back-to-back sweep that tore the adapter down repeatedly
    # and sabotaged the following write. The main loop's sleep(7) spaces attempts.

# === Main Controller ===
if __name__ == '__main__':
    force = len(sys.argv) > 1 and sys.argv[1] in ('--now', '--force')
    if not force and time.localtime().tm_hour != TARGET_HOUR:
        # Not 18:00 Ljubljana — cron fires hourly, so exit quietly otherwise.
        # Pass --now to run the sweep immediately (for testing).
        raise SystemExit(0)

    logging.info(u"🌙🌙🌙 18:00 Ljubljana — gatttool sweep over cameras 6-13 🌙🌙🌙")

    logging.info("Bluetooth {}".format(bt_adapters.describe()))

    # Cameras 6-13 (indices 5..12), queued exactly like py_new queues a camera.
    sweep_macs = KNOWN_CAMERAS[5:]
    macs_to_process = {mac: 1 for mac in sweep_macs}
    attempt_counter = {mac: 0 for mac in sweep_macs}

    # Same retry loop as py_new's main controller. Only difference: it ends when
    # every camera is done (success removes it in run_gatttool; a 5-times failure
    # removes it here), since there is no scanner to clear macs_to_process.
    while macs_to_process:
        for mac in list(macs_to_process.keys()):
            if attempt_counter.get(mac, 0) >= 5:
                cam_num = CAMERA_MAP.get(mac, mac)
                logging.warning(u"❌❌❌❌❌CAMERA {} ({}) FAILED 5 TIMES.❌❌❌❌❌❌❌❌❌❌".format(cam_num, mac))
                send_failure_email(cam_num)
                attempt_counter[mac] = 0
                del macs_to_process[mac]
                continue

            attempt_counter[mac] = attempt_counter.get(mac, 0) + 1
            run_gatttool(mac, macs_to_process, attempt_counter)
            time.sleep(7)

    logging.info(u"🌙 Sweep complete.")
