from datetime import datetime, timedelta
import time
import subprocess
from bluepy.btle import Scanner
import logging
from pathlib import Path
import smtplib
from email.message import EmailMessage

# Setup logging
log_file = Path("/home/pi/Desktop/new_log.log")
logging.basicConfig(
    filename=str(log_file),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Email config
EMAIL_FROM = "planica.zipline@gmail.com"
EMAIL_TO = "planica.zipline@gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_PASS = "bjrwefqlgikznpfm"

KNOWN_CAMERAS = [
    'fb:9a:49:68:6b:f2', 'd5:ed:26:d6:c2:3b', 'dd:30:f0:c9:83:f0', 'e7:5c:2c:64:3c:1c',
    'ff:30:3a:eb:6b:d3', 'ef:be:79:67:78:46', 'f4:8f:f7:98:81:3a', 'f3:f6:b0:75:90:61',
    'ee:d5:4d:88:77:ff', 'ec:0c:e7:74:38:fc', 'ee:ea:a6:26:99:7e', 'e7:95:be:d1:c6:61',
    'cc:0b:1a:fd:8b:b6'
]
KNOWN_CAMERAS = [x.lower() for x in KNOWN_CAMERAS]
CAMERA_MAP = {mac: i+1 for i, mac in enumerate(KNOWN_CAMERAS)}
camera_state = {}

def send_failure_email(camera_number):
    msg = EmailMessage()
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = "GATT write failed 5 times for Camera {}".format(camera_number)
    msg.set_content("All 5 attempts to write to camera {} failed at {}.".format(
        camera_number,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(EMAIL_FROM, SMTP_PASS)
            server.send_message(msg)
        logging.info("Sent failure email for camera {}".format(camera_number))
    except Exception as e:
        logging.error("Failed to send email for camera {}: {}".format(camera_number, e))

def run_gatttool(mac):
    cam_num = CAMERA_MAP.get(mac, mac)
    logging.info("Running gatttool for Camera {} ({})".format(cam_num, mac))
    success = False

    for attempt in range(1, 6):
        cmd = [
            "timeout", "5",
            "gatttool", "-t", "random", "-b", mac,
            "--char-write-req", "-a", "0x2f", "-n", "03170101"
        ]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            out_bytes, _ = proc.communicate()
            try:
                output = out_bytes.decode("utf-8", "ignore")
            except:
                output = str(out_bytes)
            retcode = proc.returncode
        except Exception as e:
            logging.error("Exception calling gatttool for {}: {}".format(mac, e))
            output = ""
            retcode = -1

        if "Characteristic value was written successfully" in output:
            logging.info("Success on attempt {} for {}".format(attempt, mac))
            success = True
            break
        else:
            logging.info("No success on attempt {} for {}, output: {}".format(attempt, mac, output.strip()))
            try:
                subprocess.run(["bluetoothctl", "disconnect", mac],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               check=False)
            except Exception as e:
                logging.warning("Failed to run bluetoothctl disconnect: {}".format(e))

    if not success:
        return 0
        logging.warning("All 5 attempts failed for Camera {} ({})".format(cam_num, mac))
        send_failure_email(cam_num)
    else:
        return 1

while True:
    now = datetime.now()
    scanner = Scanner()
    try:
        devices = scanner.scan(2)
    except Exception as e:
        logging.warning("Scan failed: {}".format(e))
        time.sleep(5)
        continue

    detected = {
        dev.addr.lower(): dev.rssi
        for dev in devices
        if dev.addr.lower() in KNOWN_CAMERAS
    }

    for mac, rssi in detected.items():
        cam_num = CAMERA_MAP.get(mac, mac)

        state = camera_state.get(mac, {
            'last_seen': None,
            'seen_weak_after_absence': False,
            'gatt_triggered': False
        })

        if state['last_seen'] is None or (now - state['last_seen']) > timedelta(minutes=10):
            state['seen_weak_after_absence'] = False
            state['gatt_triggered'] = False
            logging.info("Camera {} ({}) was absent >10 minutes — resetting event tracking.".format(cam_num, mac))

        if rssi < -80 and not state['seen_weak_after_absence'] and not state['gatt_triggered']:
            state['seen_weak_after_absence'] = True
            logging.info("Camera {} ({}) detected weak after absence (RSSI={})".format(cam_num, mac, rssi))

        elif rssi >= -80 and state['seen_weak_after_absence'] and not state['gatt_triggered']:
            logging.info("Camera {} ({}) improved signal — triggering GATT (RSSI={})".format(cam_num, mac, rssi))
            if run_gatttool(mac):
                state['gatt_triggered'] = True

        elif not state['last_seen'] or (now - state['last_seen']) > timedelta(minutes=10):
            state['gatt_triggered']=True
            logging.info("Camera {} ({}) appeared with RSSI {}".format(cam_num, mac, rssi))

        state['last_seen'] = now
        camera_state[mac] = state

    time.sleep(5)
