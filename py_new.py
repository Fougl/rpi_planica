# -*- coding: utf-8 -*-
from multiprocessing import Process, Manager, Value
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
import signal
import threading
import bt_adapters

# The UB500 on USB scans; the onboard radio does the GATT writes, so a write can
# never stall the scan. Pin either role with BT_SCAN_HCI / BT_GATT_HCI in .env.
SCAN_HCI, GATT_HCI = bt_adapters.resolve()
GATT_ADDR = bt_adapters.address(GATT_HCI)

# bluepy's scan() reads from bluepy-helper with a blocking read. When the helper
# wedges (the "Failed to execute management command 'scanend'" state) that read
# never returns: nothing is raised, so `except Exception` never fires, nothing is
# logged, and systemd still reports the service active because the parent process
# is alive. The scanner is then dead until someone power-cycles the Pi -- observed
# 2026-09-04 18:11 -> 2026-09-05 14:49, a full morning with no camera triggered.
# A scan takes ~4 s, so nothing completing for this long means wedged, not quiet.
SCAN_STALE_AFTER = int(os.environ.get("BT_SCAN_STALE_AFTER", "120"))

# A scan that completes and hears NOTHING is invisible to the watchdog above:
# scan() returns, the heartbeat refreshes, systemd is happy, and the log reads
# "scan alive - heard 0 of 14" forever. That is what a dead dongle looks like,
# and it is indistinguishable in the log from a quiet night -- 2026-09-12, the
# radio hearing zero BLE devices of any kind while the service reported itself
# healthy. A BLE radio anywhere near people hears *something*, so hearing no
# device at all -- not no camera, no device -- for this long means the radio is
# deaf, and that is worth an email.
# Resetting is cheap, silent and harmless, so do it fast: the radio normally
# hears 27-38 BLE devices in every 4-second scan, so two empty scans in a row
# already means the controller has latched. A false positive costs two seconds
# and a log line, so there is no reason to wait longer than that.
RADIO_QUIET_RESET = int(os.environ.get("BT_RADIO_QUIET_RESET", "10"))

# Mailing is the expensive part, so that waits until the reset has clearly
# failed to fix it.
RADIO_DEAF_AFTER = int(os.environ.get("BT_RADIO_DEAF_AFTER", "300"))

# And keep saying so. A single alert per episode means one email on the first
# day and silence for the rest of the outage.
DEAF_REMIND = int(os.environ.get("BT_DEAF_REMIND", "3600"))

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

# === Generic Alert Email ===
def send_alert(subject, body):
    """Fire and forget. NEVER blocks the caller.

    This is called from inside the scanner process, and smtplib with no timeout
    can hang for minutes on a Pi whose WiFi has dropped -- which would stall the
    scan loop, freeze last_seen, and have the watchdog restart the service. The
    alert would then be causing the outage it exists to report. So it runs on a
    daemon thread with a socket timeout, and the scan carries on regardless of
    whether the mail ever leaves.
    """
    def _send():
        msg = EmailMessage()
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as server:
                server.login(EMAIL_FROM, SMTP_PASS)
                server.send_message(msg)
            logging.info("Sent alert: {}".format(subject))
        except Exception as e:
            logging.error("Failed to send alert '{}': {}".format(subject, e))

    threading.Thread(target=_send, daemon=True).start()

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
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as server:
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

# === Scan Adapter Reset ===
def reset_scan_adapter(why, mail=True):
    """Power-cycle the scanning adapter. Never touches the GATT adapter.

    A process killed while a scan is enabled leaves the controller latched:
    it answers every later LE Set Extended Scan Enable (opcode 0x2042) with
    Command Disallowed, which the kernel logs as 'failed: -16' (EBUSY). bluepy
    then asks, is refused, waits its 4 seconds and returns an empty list -- no
    exception, no log line. The service looks perfectly healthy and hears
    nothing, forever, because nothing in the system ever resets the adapter.
    Observed on hci1 from 2026-09-06 to 2026-09-12: six days of
    'scan alive - heard 0 of 14' with the cameras demonstrably powered on.

    hciconfig down/up clears the latch. Doing it at every start means any kill,
    crash or restart self-heals on the way back up instead of needing a person.
    """
    hci = "hci{}".format(SCAN_HCI)
    logging.info("Resetting {} before scanning ({})".format(hci, why))
    for action in ("down", "up"):
        try:
            subprocess.run(["hciconfig", hci, action], timeout=10, check=False)
        except Exception as e:
            logging.error("hciconfig {} {} failed: {}".format(hci, action, e))
    time.sleep(1)

    # The routine recovery reset passes mail=False and stops here. A reset that
    # works is not news, and mailing every one of them is what makes alerts worth
    # ignoring. Only the deaf alarm mails, once the resets have clearly failed.
    if not mail:
        return

    send_alert(
            "Planica Pi: reset {} - {}".format(hci, why),
            "The scanning adapter {} was reset (hciconfig down/up) on {}.\n\n"
            "Reason: {}\n"
            "Time:   {}\n\n"
            "A reset at startup is routine after a restart. A reset for a deaf "
            "radio, or several of these in a day, means the controller keeps "
            "latching and the dongle needs looking at:\n\n"
            "  bash /home/pi/Desktop/bt_diag.sh\n"
            "  dmesg -T | grep 0x2042 | tail\n".format(
                hci, os.uname()[1], why,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

def scan_refused_count(idx):
    """How many times the kernel has logged this adapter refusing a scan.

    This is what separates a broken radio from a quiet one. Hearing zero BLE
    devices proves nothing on its own -- with every GoPro switched off and
    nobody around, zero is the correct answer, and resetting the adapter or
    mailing about it would be a false alarm.

    A LATCHED controller is different and says so: every LE Set Extended Scan
    Enable (opcode 0x2042) comes back Command Disallowed, which the kernel logs
    as 'failed: -16'. So the test is not "did we hear nothing", it is "did we
    hear nothing WHILE the kernel was refusing our scans". Returns None if
    dmesg cannot be read, and the caller falls back to the time-based rule.
    """
    try:
        out = subprocess.check_output(["dmesg"], stderr=subprocess.DEVNULL,
                                      timeout=10).decode("utf-8", "ignore")
    except Exception:
        return None
    return out.count("hci{}: Opcode 0x2042 failed".format(idx))


def adapter_is_up(hci):
    """True if hciconfig reports the adapter UP RUNNING."""
    try:
        out = subprocess.check_output(["hciconfig", hci],
                                      stderr=subprocess.STDOUT).decode("utf-8", "ignore")
    except Exception as e:
        logging.error("hciconfig {} failed: {}".format(hci, e))
        return False
    return "UP RUNNING" in out


# === Scanner Process ===
def scanner_loop(macs_to_process, attempt_counter, heartbeat):
    # Only touch the radio if it is actually down. Resetting a working adapter
    # at every start is noise, and it would mail on every restart. A controller
    # latched with a stuck scan still reports UP RUNNING, so that case is caught
    # by the deaf alarm below instead -- by the radio hearing nothing, which is
    # the symptom that actually matters.
    _hci = "hci{}".format(SCAN_HCI)
    if adapter_is_up(_hci):
        logging.info("{} is UP RUNNING - leaving it alone".format(_hci))
    else:
        reset_scan_adapter("{} was not UP RUNNING at startup".format(_hci))
    scanner = Scanner(SCAN_HCI)

    # And do not create that state ourselves. The watchdog kills this process
    # with SIGTERM mid-scan; without this the controller is left scanning with
    # nobody collecting, which is exactly the latch described above.
    def _clean_stop(signum, frame):
        try:
            scanner.stop()
        except Exception:
            pass
        os._exit(0)
    try:
        signal.signal(signal.SIGTERM, _clean_stop)
        signal.signal(signal.SIGINT, _clean_stop)
    except Exception as e:
        logging.warning("Could not install clean-stop handler: {}".format(e))

    last_seen = {}
    first_rssi = {}
    rssi_state = {}
    absence_time = {}
    # Logging only -- none of these feed the state machine below.
    last_rssi = {}        # last RSSI that counted, for the ABSENT line
    absent_logged = set() # cameras already reported ABSENT this absence
    last_any_device = time.time()  # any BLE device at all, camera or not
    deaf_since = None              # when this deaf episode started, None if hearing
    deaf_last_alert = 0.0          # last deaf email, so the reminders are paced
    deaf_alerts = 0                # how many reminders this episode
    last_reset = 0.0               # paces the silent recovery resets
    last_refused = None            # kernel refusal count at the previous scan

    while True:
        try:
            devices = scanner.scan(4)
            now = datetime.now()
            # Proof of life for the watchdog in the main controller. Only a
            # scan that actually returned refreshes this.
            heartbeat.value = time.time()

            # Deaf-radio alarm. Logging and email only -- it never touches
            # last_seen, rssi_state or macs_to_process, so detection behaves
            # exactly as before whether this fires or not.
            #
            # Hearing nothing is NOT the signal. With every GoPro switched off
            # and nobody around, zero devices is the correct answer and acting
            # on it would be a false alarm. The signal is the kernel refusing
            # our scans (scan_refused_count) while we hear nothing: that is a
            # latched controller and nothing else looks like it.
            refused = scan_refused_count(SCAN_HCI)
            newly_refused = False
            if refused is not None:
                # Only a rising count means new refusals. dmesg wraps, so a
                # falling count is the buffer rolling, not the radio.
                if last_refused is not None and refused > last_refused:
                    newly_refused = True
                last_refused = refused

            if devices:
                last_any_device = time.time()
                if deaf_since is not None:
                    down = (time.time() - deaf_since) / 60.0
                    logging.error("RADIO RECOVERED on hci{} after {:.0f}min - {} BLE devices this scan".format(
                        SCAN_HCI, down, len(devices)))
                    # Only worth a mail if a deaf mail went out; a silent reset
                    # that worked is not news.
                    if deaf_alerts:
                        send_alert(
                            "Planica Pi: radio RECOVERED on hci{}".format(SCAN_HCI),
                            "hci{} is hearing again -- {} BLE devices in this scan, after "
                            "{:.0f} minutes latched.\n\nCameras can be triggered again.\n\n{}\n".format(
                                SCAN_HCI, len(devices), down,
                                datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                    deaf_since = None
                    deaf_alerts = 0
            else:
                quiet = time.time() - last_any_device
                # With no dmesg to ask, fall back to a long silence for the RESET
                # only -- a reset is harmless. It never mails on that guess: a
                # quiet night and a dead radio look identical without the kernel
                # signal, and a false alarm is worse than a late one.
                if refused is None:
                    latched = quiet > RADIO_DEAF_AFTER
                else:
                    latched = newly_refused

                if latched:
                    if deaf_since is None:
                        deaf_since = time.time()

                    # Fast, silent recovery, retried on the same cadence for as
                    # long as the kernel keeps refusing.
                    if (time.time() - last_reset) > RADIO_QUIET_RESET:
                        last_reset = time.time()
                        logging.warning("hci{} latched - kernel refusing scans, nothing heard for {:.0f}s - resetting it".format(
                            SCAN_HCI, quiet))
                        reset_scan_adapter("kernel refusing scans, nothing heard for {:.0f}s".format(quiet), mail=False)

                    # Mail only once the resets have clearly failed, then keep
                    # saying so. One alert per episode meant one email on the
                    # first day and silence for the rest of the outage.
                    stuck = time.time() - deaf_since
                    first = deaf_alerts == 0
                    if stuck > RADIO_DEAF_AFTER and refused is not None and (first or (time.time() - deaf_last_alert) > DEAF_REMIND):
                        deaf_last_alert = time.time()
                        deaf_alerts += 1
                        logging.error("RADIO DEAF (alert #{}) - hci{} latched for {:.0f}min and resets are not clearing it. No camera can be triggered.".format(
                            deaf_alerts, SCAN_HCI, stuck / 60.0))
                        send_alert(
                            "Planica Pi: radio deaf on hci{} ({:.0f}min, alert #{})".format(
                                SCAN_HCI, stuck / 60.0, deaf_alerts),
                            "hci{} has been latched for {:.0f} minutes: the kernel is refusing "
                            "every scan (Opcode 0x2042 failed: -16) and resetting the adapter "
                            "every {}s is not clearing it.\n\nThe service is running and looks "
                            "healthy. No camera can be triggered in this state.\n\nThis is "
                            "alert #{} -- they repeat every {:.0f} min until it recovers, so "
                            "silence after this means the mail stopped working, not the "
                            "radio.\n\nOn the Pi:\n  bash /home/pi/Desktop/bt_diag.sh\n"
                            "  dmesg -T | grep 0x2042 | tail\n  (unplug and replug the dongle)\n\n{}\n".format(
                                SCAN_HCI, stuck / 60.0, RADIO_QUIET_RESET, deaf_alerts,
                                DEAF_REMIND / 60.0,
                                datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

            visible = {}

            # Report the absent transition once. This is the same 5-minute test
            # the state machine applies on the next sighting, logged when it
            # becomes true instead of silently, so the log shows whether a
            # camera ever became eligible again.
            for mac, prev in list(last_seen.items()):
                if (now - prev) > timedelta(minutes=5) and mac not in absent_logged:
                    absent_logged.add(mac)
                    logging.info("Camera {} ({}) is now ABSENT - unseen {:.0f}min, last RSSI={} - will re-arm on return".format(
                        CAMERA_MAP.get(mac, mac), mac, (now - prev).total_seconds() / 60.0, last_rssi.get(mac)))

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
                visible[mac] = dev.rssi

                rssi = dev.rssi
                last_rssi[mac] = rssi
                absent_logged.discard(mac)
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
    manager = Manager()
    macs_to_process = manager.dict()
    attempt_counter = manager.dict()

    heartbeat = Value('d', time.time())
    scanner_proc = Process(target=scanner_loop, args=(macs_to_process, attempt_counter, heartbeat))
    scanner_proc.start()

    try:
        while True:
            # Watchdog. Two ways the scanner stops while systemd still reports
            # the service active: bluepy blocks forever inside scan(), or the
            # child process dies and this loop keeps spinning over an empty
            # queue. Either way no camera is triggered again until the Pi is
            # power-cycled by hand. Exit non-zero and let Restart=always bring
            # the service back with a fresh bluepy-helper.
            stalled = time.time() - heartbeat.value
            if not scanner_proc.is_alive() or stalled > SCAN_STALE_AFTER:
                logging.error("Scanner stalled: {:.0f}s since the last completed scan, alive={} - restarting service".format(
                    stalled, scanner_proc.is_alive()))
                scanner_proc.terminate()
                scanner_proc.join(5)
                os._exit(1)

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

            # Nothing queued: without this the loop spins a core at 100%.
            time.sleep(1)

    except KeyboardInterrupt:
        logging.info("Interrupted by user")
    finally:
        scanner_proc.terminate()
        scanner_proc.join()
        logging.info("Scanner process terminated")
