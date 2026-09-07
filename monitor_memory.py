# -*- coding: utf-8 -*-
import os
import smtplib
import subprocess
import logging
from email.message import EmailMessage
from datetime import datetime
from pathlib import Path

EMAIL_FROM = "planica.zipline@gmail.com"
EMAIL_TO = "planica.zipline@gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_PASS = os.environ["SMTP_PASS"]

WARN_MB = 80    # send email warning below this
REBOOT_MB = 40  # reboot below this

log_file = Path("/home/pi/memory_monitor.log")
logging.basicConfig(
    filename=str(log_file),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def get_free_mb():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    return None

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

free_mb = get_free_mb()
if free_mb is None:
    logging.error("Could not read memory info")
elif free_mb < REBOOT_MB:
    logging.warning("Critical memory: {}MB free — sending email and rebooting".format(free_mb))
    send_email(
        "RPi CRITICAL: rebooting due to low memory",
        "Free memory dropped to {}MB at {}. Rebooting now.".format(free_mb, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    subprocess.run(["sudo", "reboot"])
elif free_mb < WARN_MB:
    logging.warning("Low memory: {}MB free — sending warning email".format(free_mb))
    send_email(
        "RPi WARNING: low memory ({}MB free)".format(free_mb),
        "Free memory is {}MB at {}. Consider checking the Pi if this persists.".format(free_mb, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
else:
    logging.info("Memory OK: {}MB free".format(free_mb))
