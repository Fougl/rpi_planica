# -*- coding: utf-8 -*-
from multiprocessing import Process, Manager
from datetime import datetime, timedelta
import os
import time
import subprocess
import logging
from pathlib import Path
import smtplib
from email.message import EmailMessage
from bluepy.btle import Scanner
import pexpect
import sys
import bt_adapters

# The UB500 on USB scans; the onboard radio does the GATT writes, so a write can
# never stall the scan. Pin either role with BT_SCAN_HCI / BT_GATT_HCI in .env.
SCAN_HCI, GATT_HCI = bt_adapters.resolve()
GATT_ADDR = bt_adapters.address(GATT_HCI)

# The dongle hears roughly 15 dB further than the onboard radio the absent/weak/
# strong logic was tuned against. A camera at the top of the line is still heard
# by it, so last_seen keeps refreshing, the camera never goes absent, and it can
# never be re-armed -- nothing fires when the rider comes down. Sightings below
# this floor are dropped before the state machine sees them, so the dongle hears
# like the old radio did. Override with BT_SCAN_FLOOR in .env; diag.sh's census
# gives the real number.
SCAN_FLOOR = int(os.environ.get("BT_SCAN_FLOOR", "-85"))

# === GoPro BLE Busy Query ===
class StdoutLogger(object):
    def write(self, data):
        sys.stdout.write(data.decode('utf-8', errors='ignore'))
        sys.stdout.flush()
    def flush(self):
        pass

def query_gopro_busy(mac):
    child = pexpect.spawn("gatttool -i hci{} -t random -b {} -I".format(GATT_HCI, mac), timeout=15)
    child.logfile = StdoutLogger()

    try:
        child.expect(r'\[LE\]>')
        child.sendline("connect")
        child.expect("Connection successful", timeout=5)

        child.sendline("char-write-req 0x0039 021308")
        child.expect("Characteristic value was written successfully", timeout=3)
        child.expect(r'\[LE\]>')

        child.sendline("char-write-cmd 0x003c 0100")
        child.expect(r'\[LE\]>')

        child.sendline("char-write-req 0x0039 021308")
        child.expect("Characteristic value was written successfully", timeout=3)
        time.sleep(0.5)

        try:
            child.expect("value:", timeout=10)
            output = child.before.decode() + child.after.decode() + child.read(20).decode()
            logging.info("Raw notification received from {}:\n{}".format(mac, output.strip()))
        except pexpect.TIMEOUT:
            logging.warning("No notification received from {} (timeout).".format(mac))
    except Exception as e:
        logging.error("Error in query_gopro_busy for {}: {}".format(mac, e))
    finally:
        child.sendline("exit")
        child.close()

# === Setup Logging ===
# log_file = Path("/home/pi/Desktop/new_log.log")
# logging.basicConfig(
#     filename=str(log_file),
#     level=logging.INFO,
#     format="%(asctime)s [%(levelname)s] %(message)s"
# )

log_file = Path("/home/pi/Desktop/new_log.log")

# Create file handler with UTF-8 encoding manually
file_handler = logging.FileHandler(str(log_file), mode='a', encoding='utf-8')
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"
))

# Setup logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)

# Optional: also log to console (UTF-8 capable terminal)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"
))
logger.addHandler(console_handler)

# === Email Configuration ===
EMAIL_FROM = "planica.zipline@gmail.com"
EMAIL_TO = "planica.zipline@gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_PASS = os.environ["SMTP_PASS"]

# === Known Camera MACs ===
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

# === Email Failure Alert ===
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

# === GATT Execution ===
def run_gatttool(mac, macs_to_process, attempt_counter):
    cam_num = CAMERA_MAP.get(mac, mac)
    if attempt_counter[mac]==1:
        logging.info(u"🚀🚀🚀🚀🚀 START GATTTOOL FOR CAMERA {} ({}) 🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀".format(cam_num, mac))
    logging.info("Running gatttool for Camera {} ({})".format(cam_num, mac))

    cmd = [
        "timeout", "--foreground", "3",
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

    # Disconnect on the GATT adapter only. bluetoothctl acts on its default
    # controller, which may be the dongle, so the controller is selected first.
    # On failure this resets that one adapter -- never `systemctl restart
    # bluetooth`, which would take the dongle down mid-scan.
    try:
        script = "select {}\ndisconnect {}\nquit\n".format(GATT_ADDR, mac)
        subprocess.run(["bluetoothctl"],
                       input=script.encode(),
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       timeout=3,
                       check=False)
    except Exception as e:
        logging.error("Failed bluetoothctl disconnect for {}: {}".format(mac, e))
        logging.warning("Resetting hci{} only; the scan on hci{} keeps running.".format(GATT_HCI, SCAN_HCI))
        subprocess.run(["hciconfig", "hci{}".format(GATT_HCI), "reset"])
        time.sleep(2)
    logging.info("Finished bluetoothctl disconnect for {}".format(mac))

# === Scanner Process ===
def scanner_loop(macs_to_process, attempt_counter):
    scanner = Scanner(SCAN_HCI)
    last_seen = {}
    first_rssi = {}
    rssi_state = {}
    absence_time = {}

    while True:
        try:
            devices = scanner.scan(4)
            now = datetime.now()
            
            for mac in list(macs_to_process.keys()):
                prev = last_seen.get(mac)
                if prev and (now - prev) > timedelta(minutes=2):
                    cam_num = CAMERA_MAP.get(mac, mac)
                    del macs_to_process[mac]
                    attempt_counter[mac] = 0
                    logging.info(u"🗑️🗑️🗑️🗑️REMOVED - CAMERA {} NOT VISIBLE FOR SOME TIME.".format(cam_num))

            for dev in devices:
                mac = dev.addr.lower()
                if mac not in KNOWN_CAMERAS:
                    continue
                if dev.rssi < SCAN_FLOOR:
                    continue  # too faint for the old radio; treat as not seen

                rssi = dev.rssi
                cam_num = CAMERA_MAP.get(mac, mac)
                prev = last_seen.get(mac)

                if prev and (now - prev) > timedelta(minutes=1) and mac in macs_to_process:
                    del macs_to_process[mac]

                if prev is None or (now - prev) > timedelta(minutes=5) or rssi_state.get(mac) == 'weak':
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
    logging.info(u"▶▶▶▶▶▶▶▶Script started.▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶")
    logging.info("Bluetooth {}".format(bt_adapters.describe()))
    logging.info("Scan floor: {} dBm (sightings below this are ignored)".format(SCAN_FLOOR))
    manager = Manager()
    macs_to_process = manager.dict()
    attempt_counter = manager.dict()

    scanner_proc = Process(target=scanner_loop, args=(macs_to_process, attempt_counter,))
    scanner_proc.start()

    try:
        while True:
            for mac in list(macs_to_process.keys()):
                if attempt_counter.get(mac, 0) >= 5:
                    cam_num = CAMERA_MAP.get(mac, mac)
                    logging.warning(u"❌❌❌❌❌CAMERA {} ({}) FAILED 5 TIMES.❌❌❌❌❌❌❌❌❌❌".format(cam_num, mac))
                    send_failure_email(cam_num)
                    attempt_counter[mac] = 0
                    # Stop retrying this camera. Previously it only reset the
                    # counter, so the loop immediately began another five
                    # attempts and another email, indefinitely. That never
                    # showed on the old single-radio Pi because gatttool broke
                    # the scan, last_seen went stale, and scanner_loop removed
                    # the camera as "not visible" after 2 minutes -- one email,
                    # by accident. With the dongle scanning uninterrupted the
                    # camera is never falsely considered gone, so the loop ran
                    # forever (87 attempts, 15 emails, observed 2026-09-02).
                    # run_gatttool_all.py already does this.
                    # A later weak->strong transition re-queues the camera, so
                    # a recovered camera is still picked up.
                    del macs_to_process[mac]
                    continue

                attempt_counter[mac] = attempt_counter.get(mac, 0) + 1
                #Process(target=run_gatttool, args=(mac, macs_to_process, attempt_counter)).start()
                run_gatttool(mac, macs_to_process, attempt_counter)
                time.sleep(7)

    except KeyboardInterrupt:
        logging.info("Interrupted by user")
    finally:
        scanner_proc.terminate()
        scanner_proc.join()
        logging.info("Scanner process terminated")
