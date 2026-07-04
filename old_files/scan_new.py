#!/usr/bin/env python3 
import os
import subprocess
import logging
from pathlib import Path
from datetime import datetime, timedelta
from bluepy.btle import Scanner
import smtplib
from email.message import EmailMessage

# Setup base directory and file paths using pathlib and environment variable
base_dir = Path(__file__).parent.resolve()

time_file = base_dir / "time.txt"
last_run_time_file = base_dir / "last_run_time.txt"
all_trigger_events_file = base_dir / "all_trigger_events.txt"
# res_new.txt is no longer written

# Configure logging
log_file = base_dir / "script_log.txt"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(log_file), encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# SMTP/email configuration (do not change)
EMAIL_FROM = "planica.zipline@gmail.com"
EMAIL_TO   = "planica.zipline@gmail.com"
SMTP_HOST  = "smtp.gmail.com"
SMTP_PORT  = 465
SMTP_PASS  = os.environ["SMTP_PASS"]

now = datetime.now()

scanner = Scanner()
devices = scanner.scan(2)
all_cam=['fb:9a:49:68:6b:f2',
         'd5:ed:26:d6:c2:3b',
         'dd:30:f0:c9:83:f0',
         'e7:5c:2c:64:3c:1c',
         'ff:30:3a:eb:6b:d3',
         'ef:be:79:67:78:46',
         'f4:8f:f7:98:81:3a',
         'f3:f6:b0:75:90:61',
         'ee:d5:4d:88:77:ff',
         'ec:0c:e7:74:38:fc',
         'ee:ea:a6:26:99:7e',
         'e7:95:be:d1:c6:61',
         'cc:0b:1a:fd:8b:b6'] 

all_rssi=[x.rssi for x in devices]
all_devices=[x.addr for x in devices]

detected_cams=set(all_cam)&set(all_devices)
detected_cam_index=[i for i,x in enumerate(all_devices) if x in all_cam]
detected_rssi=[x for i,x in enumerate(all_rssi) if i in detected_cam_index]

rssi_threshold=[i for i,x in enumerate(detected_rssi) if x>-80]

cam_threshold=[x for i,x in enumerate(detected_cams) if i in rssi_threshold]

cam_index=[i for i,x in enumerate(all_cam) if x in cam_threshold]

with time_file.open() as f:
    content=f.readlines()
    
time=[x.strip() for x in content]
time_index=[i for i,x in enumerate(time) if (now-datetime.strptime(x, "%m/%d/%Y, %H:%M:%S")).total_seconds()/60.0>10]

for i in cam_index:
    time[i]=now.strftime("%m/%d/%Y, %H:%M:%S")
    
with time_file.open('w') as output:
    output.write('\n'.join(map(str, time)))

eligible_cams=set(cam_index)&set(time_index)
if eligible_cams:
    logging.info("Eligible Cameras: %s", eligible_cams)
res_new=[x for i,x in enumerate(all_cam) if i in eligible_cams]

res_new=[i.upper() for i in res_new]

now=datetime.now()
time_str=now.strftime("%m/%d/%Y, %H:%M:%S")
with last_run_time_file.open('w') as output:
    output.write(time_str)
    
eligible_cams_events=[str(s)+'_'+time_str for s in eligible_cams]

if all_trigger_events_file.exists():
    with all_trigger_events_file.open() as f:
        content=f.readlines()
    wakeup=[x.strip() for x in content]
else:
    wakeup=[]
wakeup.extend(eligible_cams_events)
with all_trigger_events_file.open('w') as output:
    output.write('\n'.join(map(str, wakeup)))

if res_new:
    

    def get_yesterday_stamp():
        yday = datetime.now() - timedelta(days=1)
        return yday.strftime("%m/%d/%Y, %H:%M:%S")

    def send_failure_email(gopro):
        msg = EmailMessage()
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        
        msg["Subject"] = "GATT write failed 5 times for {}".format(gopro)
        # Replace f-string with .format()
        msg.set_content("All 5 attempts to write to camera {} failed at {}.".format(
            gopro,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

        try:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
                server.login(EMAIL_FROM, SMTP_PASS)
                server.send_message(msg)
            logging.info("Sent failure email for %s", gopro)
        except Exception as e:
            logging.error("Failed to send email for %s: %s", gopro, e)

    for mac_upper in res_new:
        mac = mac_upper.lower()
        if mac not in all_cam:
            logging.warning("MAC %s from res_new not in all_cam, skipping", mac)
            continue

        idx = all_cam.index(mac)
        logging.info("Processing GATT write for MAC %s at index %d", mac, idx)
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
                logging.error("Exception calling gatttool for %s: %s", mac, e)
                output = ""
                retcode = -1

            if "Characteristic value was written successfully" in output:
                logging.info("Success on attempt %d for %s", attempt, mac)
                success = True
                break
            else:
                logging.info(
                    "No success on attempt %d for %s, output: %s",
                    attempt, mac, output.strip()
                )
                # Force a disconnect before the next retry
                try:
                    subprocess.run(
                        ["bluetoothctl", "disconnect", mac],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False
                    )
                except Exception as e:
                    logging.warning("Failed to run bluetoothctl disconnect: %s", e)

        if not success:
            ystamp = get_yesterday_stamp()
            logging.warning("All 5 attempts failed for %s, setting time[%d] to %s", mac, idx, ystamp)
            time[idx] = ystamp
            send_failure_email(idx+1)

            with time_file.open("w") as f:
                f.write("\n".join(time))
            logging.info("Wrote updated time_file after GATT loop")
