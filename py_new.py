from multiprocessing import Process, Manager
from datetime import datetime, timedelta
import time
import subprocess
import logging
from pathlib import Path
import smtplib
from email.message import EmailMessage
from collections import defaultdict
from bluepy.btle import Scanner

# === Setup Logging ===
log_file = Path("/home/pi/Desktop/new_log.log")
logging.basicConfig(
    filename=str(log_file),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# === Email Configuration ===
EMAIL_FROM = "planica.zipline@gmail.com"
EMAIL_TO = "planica.zipline@gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_PASS = "bjrwefqlgikznpfm"

# === Known Camera MACs ===
KNOWN_CAMERAS = [
    'fb:9a:49:68:6b:f2', 'd5:ed:26:d6:c2:3b', 'dd:30:f0:c9:83:f0', 'e7:5c:2c:64:3c:1c',
    'ff:30:3a:eb:6b:d3', 'ef:be:79:67:78:46', 'f4:8f:f7:98:81:3a', 'f3:f6:b0:75:90:61',
    'ee:d5:4d:88:77:ff', 'ec:0c:e7:74:38:fc', 'ee:ea:a6:26:99:7e', 'e7:95:be:d1:c6:61',
    'cc:0b:1a:fd:8b:b6'
]
KNOWN_CAMERAS = [x.lower() for x in KNOWN_CAMERAS]
CAMERA_MAP = {mac: i + 1 for i, mac in enumerate(KNOWN_CAMERAS)}

# === Email Failure Alert ===
def send_failure_email(camera_number):
    msg = EmailMessage()
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = "GATT write failed 5 times for Camera {}".format(camera_number)
    msg.set_content("All 5 attempts to write to camera {} failed at {}.".format(
        camera_number, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(EMAIL_FROM, SMTP_PASS)
            server.send_message(msg)
        logging.info("Sent failure email for camera {}".format(camera_number))
    except Exception as e:
        logging.error("Failed to send email for camera {}: {}".format(camera_number, e))

# === GATT Execution ===
def run_gatttool(mac, macs_to_process):
    cam_num = CAMERA_MAP.get(mac, mac)
    logging.info("Running gatttool for Camera {} ({})".format(cam_num, mac))

    cmd = [
        "timeout", "--foreground", "5",
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

    success = "Characteristic value was written successfully" in output
    if success:
        logging.info("Success for {}".format(mac))
        if mac in macs_to_process:
            del macs_to_process[mac]
    else:
        logging.info("Failed for {}, output: {}".format(mac, output.strip()))

    try:
        subprocess.run(["bluetoothctl", "disconnect", mac],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       timeout=1,
                       check=False)
    except Exception as e:
        logging.info("Failed bluetoothctl disconnect for {}: {}".format(mac, e))
    logging.info("Finished bluetoothctl disconnect for {}".format(mac))

# === Scanner Process ===
def scanner_loop(macs_to_process):
    scanner = Scanner()
    last_seen = {}
    first_rssi = {}
    rssi_state = {}
    absence_time = {}

    while True:
        try:
            devices = scanner.scan(1)
            now = datetime.now()

            for dev in devices:
                mac = dev.addr.lower()
                if mac not in KNOWN_CAMERAS:
                    continue

                rssi = dev.rssi
                cam_num = CAMERA_MAP.get(mac, mac)
                prev = last_seen.get(mac)

                if prev and (now - prev) > timedelta(minutes=1) and mac in macs_to_process:
                    del macs_to_process[mac]

                if prev is None or (now - prev) > timedelta(minutes=10) or rssi_state.get(mac) == 'weak':
                    if mac not in first_rssi:
                        first_rssi[mac] = rssi
                        absence_time[mac] = now
                        if rssi >= -70:
                            logging.info("Camera {} ({}) was absent >10min, returned STRONG (RSSI={})".format(cam_num, mac, rssi))
                            rssi_state[mac] = 'strong'
                        else:
                            logging.info("Camera {} ({}) was absent >10min, returned WEAK (RSSI={})".format(cam_num, mac, rssi))
                            rssi_state[mac] = 'weak'
                    elif rssi_state.get(mac) == 'weak' and rssi >= -70:
                        logging.info("Camera {} ({}) was WEAK after absence, now STRONG — trigger (RSSI={})".format(cam_num, mac, rssi))
                        macs_to_process[mac] = 1
                        rssi_state[mac] = 'strong'
                else:
                    if mac in first_rssi:
                        del first_rssi[mac]
                        absence_time[mac] = None

                last_seen[mac] = now
        except Exception as e:
            logging.warning("Scan failed: {}".format(e))

# === Main Controller ===
if __name__ == '__main__':
    logging.info("Script started.")
    manager = Manager()
    macs_to_process = manager.dict()  # ← simulate set with dict keys
    attempt_counter = defaultdict(int)

    scanner_proc = Process(target=scanner_loop, args=(macs_to_process,))
    scanner_proc.start()

    try:
        while True:
            for mac in list(macs_to_process.keys()):
                if attempt_counter[mac] >= 5:
                    cam_num = CAMERA_MAP.get(mac, mac)
                    logging.warning("Camera {} ({}) failed 5 times. Skipping.".format(cam_num, mac))
                    send_failure_email(cam_num)
                    if mac in macs_to_process:
                        del macs_to_process[mac]
                    continue

                attempt_counter[mac] += 1
                Process(target=run_gatttool, args=(mac, macs_to_process)).start()
                time.sleep(1.5)

    except KeyboardInterrupt:
        logging.info("Interrupted by user")
    finally:
        scanner_proc.terminate()
        scanner_proc.join()
        logging.info("Scanner process terminated")
