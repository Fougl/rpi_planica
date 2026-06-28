# -*- coding: utf-8 -*-
from multiprocessing import Process, Manager
from datetime import datetime, timedelta
import time
import subprocess
import logging
from pathlib import Path
import smtplib
from email.message import EmailMessage
from bluepy.btle import Scanner, DefaultDelegate
import pexpect
import sys

# === GoPro BLE Busy Query ===
class StdoutLogger(object):
    def write(self, data):
        sys.stdout.write(data.decode('utf-8', errors='ignore'))
        sys.stdout.flush()
    def flush(self):
        pass

def query_gopro_busy(mac):
    child = pexpect.spawn("gatttool -t random -b {} -I".format(mac), timeout=15)
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

# === Email Alert ===
def send_email(subject, body):
    msg = EmailMessage()
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(EMAIL_FROM, SMTP_PASS)
            server.send_message(msg)
        logging.info("Sent email: {}".format(subject))
    except Exception as e:
        logging.error("Failed to send email: {}".format(e))

# === GATT Execution ===
def run_gatttool(mac, macs_to_process, attempt_counter):
    cam_num = CAMERA_MAP.get(mac, mac)
    if attempt_counter[mac]==1:
        logging.info(u"🚀🚀🚀🚀🚀 START GATTTOOL FOR CAMERA {} ({}) 🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀".format(cam_num, mac))
    logging.info("Running gatttool for Camera {} ({})".format(cam_num, mac))

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

    success = "Characteristic value was written successfully" in output
    if success:
        logging.info(u"🥇🥇🥇🥇🥇🥇SUCCESS FOR CAMERA{} 🥇🥇🥇🥇🥇🥇🥇🥇🥇🥇🥇🥇".format(cam_num))
        attempt_counter[mac] = 0
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
        logging.error("Failed bluetoothctl disconnect for {}: {}".format(mac, e))
        logging.warning("Possible GATT tool error for {}. Restarting Bluetooth...".format(mac))
        subprocess.run(["sudo", "systemctl", "restart", "bluetooth"])
        time.sleep(5)
    logging.info("Finished bluetoothctl disconnect for {}".format(mac))

# === Scanner Process ===
# Streaming (continuous) BLE scan — the Python equivalent of
# `sudo hcitool lescan --duplicates`. Instead of scanner.scan(4), which blocks
# for 4s and only returns the whole batch at the end, we start the scan once and
# react to each advertising report the moment it arrives via a delegate callback.
# passive=True => radio only listens (sends no scan requests) => low CPU, good
# for the Pi Zero.
class CameraDelegate(DefaultDelegate):
    def __init__(self, macs_to_process, attempt_counter, state):
        DefaultDelegate.__init__(self)
        self.macs_to_process = macs_to_process
        self.attempt_counter = attempt_counter
        self.state = state

    def handleDiscovery(self, dev, isNewDev, isNewData):
        mac = dev.addr.lower()
        if mac not in KNOWN_CAMERAS:
            return

        macs_to_process = self.macs_to_process
        last_seen = self.state['last_seen']
        first_rssi = self.state['first_rssi']
        rssi_state = self.state['rssi_state']
        absence_time = self.state['absence_time']

        rssi = dev.rssi
        now = datetime.now()
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
                    send_email(
                        "Camera {} back in range".format(cam_num),
                        "Camera {} ({}) was absent and returned with strong signal (RSSI={}) at {}.".format(cam_num, mac, rssi, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                    )
                else:
                    logging.info("Camera {} ({}) was absent >10min, returned WEAK (RSSI={})".format(cam_num, mac, rssi))
                    rssi_state[mac] = 'weak'
            elif rssi_state.get(mac) == 'weak' and rssi >= -70:
                logging.info("Camera {} ({}) was WEAK after absence, now STRONG — trigger (RSSI={})".format(cam_num, mac, rssi))
                send_email(
                    "Camera {} back in range".format(cam_num),
                    "Camera {} ({}) was absent and returned with strong signal (RSSI={}) at {}.".format(cam_num, mac, rssi, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                )
                macs_to_process[mac] = 1
                rssi_state[mac] = 'strong'
        else:
            if mac in first_rssi:
                del first_rssi[mac]
                absence_time[mac] = None

        last_seen[mac] = now


def scanner_loop(macs_to_process, attempt_counter):
    state = {
        'last_seen': {},
        'first_rssi': {},
        'rssi_state': {},
        'absence_time': {},
    }
    delegate = CameraDelegate(macs_to_process, attempt_counter, state)
    scanner = Scanner().withDelegate(delegate)

    while True:
        try:
            # Arm the controller once, then stream reports continuously.
            scanner.clear()
            scanner.start(passive=True)
            while True:
                # process() returns control every 1s so we can run housekeeping;
                # in between, handleDiscovery fires per advertisement in real time.
                scanner.process(1.0)

                now = datetime.now()
                last_seen = state['last_seen']
                for mac in list(macs_to_process.keys()):
                    prev = last_seen.get(mac)
                    if prev and (now - prev) > timedelta(minutes=2):
                        cam_num = CAMERA_MAP.get(mac, mac)
                        del macs_to_process[mac]
                        attempt_counter[mac] = 0
                        logging.info(u"🗑️🗑️🗑️🗑️REMOVED - CAMERA {} NOT VISIBLE FOR SOME TIME.".format(cam_num))
                        send_email(
                            "Camera {} not visible".format(cam_num),
                            "Camera {} ({}) has not been visible for over 2 minutes and was removed from processing at {}.".format(cam_num, mac, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                        )
        except Exception as e:
            logging.warning("Scan failed: {}".format(e))
            try:
                scanner.stop()
            except Exception:
                pass
            time.sleep(1)

# === Main Controller ===
if __name__ == '__main__':
    logging.info(u"▶▶▶▶▶▶▶▶Script started.▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶▶")
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
                    send_email(
                        "GATT write failed 5 times for Camera {}".format(cam_num),
                        "All 5 attempts to write to camera {} failed at {}.".format(cam_num, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                    )
                    attempt_counter[mac] = 0
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
