# -*- coding: utf-8 -*-
import smtplib
from email.message import EmailMessage
from datetime import datetime
import time
import os

EMAIL = "planica.zipline@gmail.com"
APP_PASSWORD = os.environ["SMTP_PASS"]
TO_EMAIL = "planica.zipline@gmail.com"
TIME_FILE = "/home/pi/Desktop/last_run_time.txt"
TIME_FORMAT = "%m/%d/%Y, %H:%M:%S"

def check_and_notify():
    if not os.path.exists(TIME_FILE):
        print("{} not found.".format(TIME_FILE))
        return

    with open(TIME_FILE, "r") as f:
        last_time_str = f.read().strip()

    try:
        last_time = datetime.strptime(last_time_str, TIME_FORMAT)
    except ValueError:
        print("Invalid date format in file.")
        return

    now = datetime.now()
    delta = (now - last_time).total_seconds()

    if delta > 180:
        print("Time delta is {} seconds. Sending email.".format(int(delta)))
        msg = EmailMessage()
        msg.set_content("The script was last run at {} - {} seconds ago, which exceeds the threshold.".format(
            last_time_str, int(delta)))
        msg["Subject"] = "RPi Alert: Script Inactive"
        msg["From"] = EMAIL
        msg["To"] = TO_EMAIL

        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
                smtp.login(EMAIL, APP_PASSWORD)
                smtp.send_message(msg)
            print("Email sent successfully.")
        except Exception as e:
            print("Failed to send email: {}".format(e))
    else:
        print("Time delta is only {} seconds. No email sent.".format(int(delta)))

# Loop forever, checking every 60 seconds
while True:
    check_and_notify()
    time.sleep(60)
