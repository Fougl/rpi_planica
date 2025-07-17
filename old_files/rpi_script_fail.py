from datetime import datetime
from email.message import EmailMessage
import smtplib
import time

msg=EmailMessage()
body=''
emailadd='planica.zipline@gmail.com'
key='bjrwefqlgikznpfm'
msg.set_content(body)
msg['from']=emailadd
msg['to']=emailadd
msg['subject']='rpi script fail'
server=smtplib.SMTP('smtp.gmail.com', 587)

while True:
    try:
        with open('/home/pi/Desktop/last_run_time.txt', 'r') as file:
            last_run_time=file.readline().strip()

        last_run_time=datetime.strptime(last_run_time, '%m/%d/%Y, %H:%M:%S')
        current_time=datetime.now()

        time_difference = current_time - last_run_time

        minute_difference = time_difference.total_seconds() / 60

        if minute_difference>2:
            server.starttls()
            server.login(emailadd, key)
            server.send_message(msg)
            server.quit()
    except:
        minute_difference=0

    time.sleep(60)
