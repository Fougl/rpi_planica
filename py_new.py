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

# The same latch has a second shape, and that one leaves no trace anywhere:
# every mgmt command comes back Success, LE Set Extended Scan Enable is
# accepted, and not one advertisement ever arrives. 2026-09-13: hci1 silent for
# 49 minutes after a restart while btmon showed seven clean scan cycles and zero
# reports, no kernel message at all -- and hciconfig down/up brought back 221
# reports in the next 20 seconds. The kernel-refusal test below cannot see that
# shape, so silence on its own now resets the radio too. A quiet night reaches
# this path as well, which is why that reset stays silent and paced, and why the
# mail still waits for proof that a reset is what brought the radio back.
# A scan takes ~4.2s, so this is "one empty scan and act". During opening hours
# a radio that hears nothing for five seconds is far more likely to be latched
# than to be sitting in a genuinely empty valley, and every second spent waiting
# is a second a rider's camera is not triggered.
RADIO_SILENT_RESET = int(os.environ.get("BT_RADIO_SILENT_RESET", "5"))

# ...but back off after each one, up to this. A quiet hour inside opening time
# still reaches this path -- a closed Monday, a lull between groups -- and
# resetting every 5s through it is pure churn on the hardware that already
# fails too often. Doubling keeps the fast first reset where it matters.
RADIO_SILENT_RESET_MAX = int(os.environ.get("BT_RADIO_SILENT_RESET_MAX", "900"))

# Mailing is the expensive part, so that waits until the reset has clearly
# failed to fix it.
RADIO_DEAF_AFTER = int(os.environ.get("BT_RADIO_DEAF_AFTER", "300"))

# A silence this long, cured by a reset, is worth an email even though it fixed
# itself. 2026-09-13 18:35 was deaf for 63s, recovered, and sent nothing --
# because the old rule waited for RADIO_DEAF_AFTER first, so the very first
# episode the detector ever caught was invisible to the person who asked to be
# told. Shorter gaps stay quiet; they are usually just a lull.
RECOVERY_MAIL_AFTER = int(os.environ.get("BT_RECOVERY_MAIL_AFTER", "30"))

# ...and space those out, so a bad afternoon cannot fill the mailbox.
RECOVERY_MAIL_GAP = int(os.environ.get("BT_RECOVERY_MAIL_GAP", "1800"))

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

def capture_deaf_snapshot(idx, urb_history=None, scan_once=None):
    """Dump what the radio looks like RIGHT NOW, before anything resets it.

    2026-09-13 could not be explained after the fact. By the time anyone looked,
    the reset had cleared whatever was stuck, and the kernel ring buffer had been
    flushed by unrelated spam. So: capture first, reset second, leave a file.

    What decides it is the URB rate. Reports of this exact dongle dying the same
    way (BlueZ issue 1500 -- TP-Link UB500 on a Pi, "after a day or two it
    detects only BR/EDR") come alongside reports of the interrupt URBs dying on
    RTL8761B. Two different faults wearing one symptom, needing different cures:

        urbnum climbing while scanning, zero advertisements -> the controller is
            latched, the USB link is healthy, hciconfig down/up is the fix;
        urbnum flat while scanning -> the USB side is dead, and only
            re-enumeration (unbind/bind, or a replug) will bring it back.

    Both measurements have to be taken WHILE A SCAN IS RUNNING, and that is the
    trap the first version fell into: this function runs inside the scan loop, so
    nothing is scanning while it works. Sampling urbnum here read "frozen" on a
    radio whose USB link was fine, and btmon recorded a radio nobody was asking
    to scan -- zero advertisements, which says nothing at all. Measured
    2026-09-13 18:36; it nearly cost a working dongle.

    So urbnum comes from `urb_history`, which the loop fills after each completed
    scan, and btmon is run across one real scan via `scan_once`.
    """
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    path = "/home/pi/diag/deaf-{}.txt".format(stamp)
    try:
        os.makedirs("/home/pi/diag")
    except OSError:
        pass

    def run(cmd, timeout=12):
        # Tolerate a non-zero exit on purpose. `timeout 6 btmon` always exits
        # 124 when the timeout fires, and check_output raises on that -- so the
        # single most important section of this file, the radio traffic, came
        # back as an error string the first time this ran, on a radio that was
        # demonstrably hearing. Here a non-zero exit is the normal case; only a
        # real exception is worth writing down.
        try:
            done = subprocess.run(cmd, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=timeout)
            return done.stdout.decode("utf-8", "ignore")
        except Exception as run_error:
            return "({} failed: {})\n".format(" ".join(cmd), run_error)

    # The URB rate across the loop's own samples, taken while scans were running.
    urb_lines = []
    previous = None
    for when, value in list(urb_history or []):
        if previous is not None and when > previous[0]:
            seconds = when - previous[0]
            delta = value - previous[1]
            urb_lines.append("  {}  urbnum {}  (+{} in {:.1f}s = {:.1f}/s)".format(
                datetime.fromtimestamp(when).strftime('%H:%M:%S'),
                value, delta, seconds, delta / seconds))
        previous = (when, value)
    if not urb_lines:
        urb_lines.append("  (no history yet -- the loop had not completed two scans)")

    # btmon only records something if a scan is actually in flight, and this
    # function runs inside the loop that would otherwise be scanning. So drive
    # one real scan through it rather than watching an idle radio.
    btmon_text = "(no scan callable was passed, so there was nothing to observe)\n"
    if scan_once is not None:
        heard = "not run"
        monitor = None
        try:
            monitor = subprocess.Popen(["btmon", "-i", str(idx)],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            time.sleep(0.5)
            try:
                found = scan_once()
                heard = len(found) if found is not None else 0
            except Exception as scan_error:
                heard = "scan raised: {}".format(scan_error)
            time.sleep(0.5)
            monitor.terminate()
            btmon_text = monitor.communicate(timeout=10)[0].decode("utf-8", "ignore")
        except Exception as monitor_error:
            btmon_text = "(btmon failed: {})\n".format(monitor_error)
            if monitor is not None:
                try:
                    monitor.kill()
                except Exception:
                    pass
        lines = btmon_text.splitlines()
        if len(lines) > 400:
            btmon_text = "\n".join(lines[:400]) + "\n(truncated)\n"
        btmon_text = "that scan returned {} devices\n\n".format(heard) + btmon_text

    try:
        with open(path, "w") as out:
            out.write("deaf snapshot {} -- hci{}\n\n".format(stamp, idx))
            out.write("urbnum, sampled by the loop after each completed scan:\n")
            out.write("\n".join(urb_lines) + "\n")
            out.write("  climbing while scanning = USB alive, controller latched"
                      " -> down/up cures it\n")
            out.write("  flat while scanning     = USB side dead"
                      " -> needs re-enumeration or a replug\n")
            out.write("  (readings taken while nothing is scanning mean nothing)\n\n")
            out.write("== hciconfig -a ==\n" + run(["hciconfig", "-a"]))
            out.write("\n== btmgmt info ==\n"
                      + run(["timeout", "8", "btmgmt", "--index", str(idx), "info"]))
            out.write("\n== lsusb ==\n" + run(["lsusb"]))
            out.write("\n== btmon across one real scan"
                      " (advertisements here mean it is NOT deaf) ==\n" + btmon_text)
            out.write("\n== dmesg tail ==\n"
                      + "\n".join(run(["dmesg", "-T"]).splitlines()[-40:]) + "\n")
        logging.error("deaf snapshot written to {}".format(path))
        return path
    except Exception as write_error:
        logging.error("could not write the deaf snapshot: {}".format(write_error))
        return None


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

    def open_scan(why):
        """Open ONE continuous scan, replacing whatever was running.

        bluepy's scan() enables scanning, disables it, and kills its helper on
        every call. At a 4s cycle that is about 20,000 teardowns and 20,000
        helper processes a day, on a controller whose firmware is known to be
        fragile -- and the deaf episodes arrive after hours of it, with no
        restart involved (2026-09-13 18:35, 1h51m after the last one). So the
        scan is opened once and kept open, and this is the only place that
        rebuilds it.

        Measured before changing anything: one scan held open for 3 minutes kept
        reporting 9-12 devices in every 30s window, so the controller's duplicate
        filter does not suppress repeats and the camera logic still sees fresh
        RSSI every cycle.
        """
        nonlocal scanner
        try:
            scanner.stop()
        except Exception:
            pass
        try:
            scanner = Scanner(SCAN_HCI)
            scanner.start()
            logging.info("continuous scan opened on hci{} ({})".format(SCAN_HCI, why))
            return True
        except Exception as open_error:
            logging.error("could not open the scan: {} ({}) - leaving it to the watchdog".format(
                open_error, why))
            return False

    def snapshot_scan():
        """One window of the already-running scan, for the deaf snapshot.

        Deliberately not scanner.scan(): that would enable and then DISABLE
        scanning, tearing down the continuous scan in the middle of the very
        episode being diagnosed.
        """
        scanner.clear()
        scanner.process(4)
        return scanner.getDevices()

    open_scan("startup")

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
    silent_backoff = RADIO_SILENT_RESET  # grows while silence persists, reset on a sighting
    urb_history = []               # (when, urbnum) after each completed scan, last 10
    last_recovery_mail = 0.0       # spaces out the "went deaf and recovered" mails

    while True:
        try:
            # One scan, held open. clear() empties bluepy's own list so that
            # getDevices() returns what THIS window heard -- without it the list
            # accumulates, and a radio that had gone completely deaf would keep
            # handing back the cameras it saw an hour ago. Every watchdog in this
            # file would then report the system healthy while no camera fires,
            # which is precisely the failure this file exists to prevent.
            scanner.clear()
            scanner.process(4)
            devices = scanner.getDevices()
            now = datetime.now()
            # Proof of life for the watchdog in the main controller. Only a
            # scan that actually returned refreshes this.
            heartbeat.value = time.time()

            # Sample urbnum right after a scan completes, so a snapshot can show
            # the URB rate DURING scanning. That rate is the only thing that
            # tells a dead USB link apart from a latched controller, and it
            # cannot be measured from inside the snapshot, which runs with this
            # loop stopped.
            try:
                with open("/sys/bus/usb/devices/1-1/urbnum") as urb_handle:
                    urb_history.append((time.time(), int(urb_handle.read().strip())))
                del urb_history[:-10]
            except Exception:
                pass

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
                # Anything heard means the next silence starts from the short
                # cadence again, however long the last quiet spell had stretched
                # the interval.
                silent_backoff = RADIO_SILENT_RESET
                if deaf_since is not None:
                    down = (time.time() - deaf_since) / 60.0
                    logging.error("RADIO RECOVERED on hci{} after {:.0f}min - {} BLE devices this scan".format(
                        SCAN_HCI, down, len(devices)))
                    # Worth a mail in two cases: a deaf alert already went out,
                    # or a silence long enough to matter ended in the scan right
                    # after a reset. That second sequence is the proof a quiet
                    # spell cannot produce, because a reset conjures no
                    # advertisers out of empty air.
                    #
                    # It has to be reported even though it fixed itself. On
                    # 2026-09-13 the detector caught its first real episode --
                    # 63s deaf, cured by the reset -- and sent nothing, because
                    # the rule then waited for RADIO_DEAF_AFTER. The one person
                    # who had asked to be told heard nothing at all. Hence
                    # RECOVERY_MAIL_AFTER, and RECOVERY_MAIL_GAP so a bad
                    # afternoon cannot turn that into a flood.
                    cured = ((time.time() - last_reset) < 12
                             and (time.time() - deaf_since) > RECOVERY_MAIL_AFTER
                             and (time.time() - last_recovery_mail) > RECOVERY_MAIL_GAP)
                    if deaf_alerts or cured:
                        last_recovery_mail = time.time()
                        send_alert(
                            "Planica Pi: radio went deaf and recovered on hci{}".format(SCAN_HCI),
                            "hci{} heard nothing for {:.0f} seconds, was reset, and is "
                            "hearing again -- {} BLE devices in the scan straight after.\n\n"
                            "It fixed itself, so nothing needs doing right now. It is worth "
                            "knowing how often this happens: every episode writes a file "
                            "under /home/pi/diag/, and the resets are in\n"
                            "  journalctl -t bt_scan_reset\n\n"
                            "Cameras can be triggered again.\n\n{}\n".format(
                                SCAN_HCI, down * 60.0, len(devices),
                                datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                    deaf_since = None
                    deaf_alerts = 0
            else:
                quiet = time.time() - last_any_device
                # Reset on either shape of the latch. The kernel refusing scans
                # is proof and is acted on at once. Hearing nothing at all is
                # only a suspicion, because a quiet night looks identical, so it
                # waits RADIO_SILENT_RESET and then resets anyway: the reset is
                # a one-second hciconfig down/up that costs nothing when the
                # suspicion is wrong, and when it is right it is the difference
                # between a 49-minute outage and a 60-second one.
                # Silence is a suspicion, not proof -- a genuinely quiet spell
                # looks identical -- so it resets and then backs off. A kernel
                # refusal is proof, and keeps the fast fixed cadence.
                latched = newly_refused or quiet > RADIO_SILENT_RESET

                if latched:
                    if deaf_since is None:
                        deaf_since = time.time()
                        # Capture before the first reset of this episode. The
                        # reset is what destroys the evidence, which is exactly
                        # why no episode so far has been explainable afterwards.
                        # One file per episode, written the moment it starts.
                        capture_deaf_snapshot(SCAN_HCI, urb_history, snapshot_scan)

                    # A confirmed latch is retried fast and on a fixed cadence:
                    # the kernel is telling us it is broken, so there is nothing
                    # to be tentative about. A bare silence backs off instead,
                    # because a quiet night lands here too and cannot be told
                    # apart -- see RADIO_SILENT_RESET_MAX.
                    pace = RADIO_QUIET_RESET if newly_refused else silent_backoff
                    if (time.time() - last_reset) > pace:
                        last_reset = time.time()
                        if newly_refused:
                            why = "kernel refusing scans, nothing heard for {:.0f}s".format(quiet)
                            next_in = RADIO_QUIET_RESET
                        else:
                            why = "nothing heard at all for {:.0f}s".format(quiet)
                            silent_backoff = min(silent_backoff * 2, RADIO_SILENT_RESET_MAX)
                            next_in = silent_backoff
                        logging.warning("hci{} looks latched - {} - resetting it (next check in {:.0f}s)".format(
                            SCAN_HCI, why, next_in))
                        reset_scan_adapter(why, mail=False)
                        # hciconfig down/up destroys the controller's scan, and
                        # with a single long-lived scan there is no next scan()
                        # call to enable it again. Without this line the FIRST
                        # reset would leave the radio deaf permanently -- a cure
                        # considerably worse than the disease.
                        open_scan("after resetting the adapter")

                    # Mail only once the resets have clearly failed, then keep
                    # saying so. One alert per episode meant one email on the
                    # first day and silence for the rest of the outage.
                    stuck = time.time() - deaf_since
                    first = deaf_alerts == 0
                    if (stuck > RADIO_DEAF_AFTER
                            and (first or (time.time() - deaf_last_alert) > DEAF_REMIND)):
                        deaf_last_alert = time.time()
                        deaf_alerts += 1
                        logging.error("RADIO DEAF (alert #{}) - hci{} latched for {:.0f}min and resets are not clearing it. No camera can be triggered.".format(
                            deaf_alerts, SCAN_HCI, stuck / 60.0))
                        send_alert(
                            "Planica Pi: radio deaf on hci{} ({:.0f}min, alert #{})".format(
                                SCAN_HCI, stuck / 60.0, deaf_alerts),
                            "hci{} has heard nothing for {:.0f} minutes and resetting the "
                            "adapter is not clearing it.\n\nThe fault comes in two shapes. "
                            "Either the kernel refuses every scan (Opcode 0x2042 failed: -16, "
                            "visible in dmesg), or every command returns Success and not one "
                            "advertisement arrives -- that second one leaves no trace anywhere "
                            "and is what happened on 2026-09-13.\n\nThe service is running and "
                            "looks healthy. No camera can be triggered in this state.\n\nThis "
                            "is alert #{} -- they repeat every {:.0f} min until it recovers, so "
                            "silence after this means the mail stopped working, not the "
                            "radio.\n\nA snapshot was written when this episode began:\n"
                            "  ls -t /home/pi/diag/ | head\n\nRead urbnum at the top of it: "
                            "climbing means the controller latched and down/up should cure it; "
                            "frozen means the USB side died and it needs re-enumeration or a "
                            "replug.\n\nAlso:\n  bash /home/pi/Desktop/bt_diag.sh\n"
                            "  journalctl -t bt_scan_reset | tail\n\n{}\n".format(
                                SCAN_HCI, stuck / 60.0, deaf_alerts,
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
            # Never spin here. bluepy raises instantly, and forever, once its
            # helper is gone: killing bluepy-helper by hand on 2026-09-13 put
            # this loop into a tight cycle that burned 7 minutes of CPU in 84
            # seconds of wall time and flooded the journal fast enough to rotate
            # older entries away -- including the evidence of earlier resets --
            # until the stale-scan watchdog finally restarted the service.
            logging.warning("Scan failed: {}".format(e))
            time.sleep(2)

            # A dead helper is never replaced by the Scanner that owned it, so
            # every later scan() raises against the same corpse and only the
            # watchdog can end it -- 120s of staleness plus RestartSec, with
            # nothing heard throughout. A fresh Scanner spawns a fresh helper,
            # so recover here instead and leave the watchdog as the backstop.
            # Only for that failure: the routine "Address type changed during
            # scan" warning is transient and must not churn the helper.
            text = str(e)
            if "Broken pipe" in text or "Helper not started" in text:
                logging.warning("scan helper is gone - reopening the scan")
                open_scan("helper died")

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
