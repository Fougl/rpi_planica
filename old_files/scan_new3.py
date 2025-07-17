from bluepy.btle import Scanner
from datetime import datetime
#with open('/home/pi/Desktop/cam_wakeup.txt') as f:
#	content=f.readlines()
#wakeup=[x.strip() for x in content]
now=datetime.now()

#now=datetime.now() 
scanner = Scanner()
devices = scanner.scan(2)
all_cam=['fb:9a:49:68:6b:f2','d5:ed:26:d6:c2:3b','dd:30:f0:c9:83:f0','e7:5c:2c:64:3c:1c','ff:30:3a:eb:6b:d3','ef:be:79:67:78:46','f4:8f:f7:98:81:3a','f3:f6:b0:75:90:61','ee:d5:4d:88:77:ff','ec:0c:e7:74:38:fc','ee:ea:a6:26:99:7e','e7:95:be:d1:c6:61','cc:0b:1a:fd:8b:b6'] 

all_rssi=[x.rssi for x in devices]
all_devices=[x.addr for x in devices]
#print(all_devices)

detected_cams=set(all_cam)&set(all_devices)
detected_cam_index=[i for i,x in enumerate(all_devices) if x in all_cam]
detected_rssi=[x for i,x in enumerate(all_rssi) if i in detected_cam_index]

#print(detected_cam_index)
#print("All MAC addresses cameras detected: ", detected_cams)
#print("RSSI for detected cameras: ", detected_rssi)

rssi_threshold=[i for i,x in enumerate(detected_rssi) if x>-80]
#print("Detected cameras with lower than 95 RSSI:", rssi_threshold)

cam_threshold=[x for i,x in enumerate(detected_cams) if i in rssi_threshold]
#print("Detected cameras with lower than 95 RSSI:", cam_threshold)

cam_index=[i for i,x in enumerate(all_cam) if x in cam_threshold]
#print("All cameras detected: ", cam_index)

with open('/home/pi/Desktop/time.txt') as f:
	content=f.readlines()
    
time=[x.strip() for x in content]
time_index=[i for i,x in enumerate(time) if (now-datetime.strptime(x, "%m/%d/%Y, %H:%M:%S")).total_seconds()/60.0>10]

#time=[datetime.now() for i,x in enumerate(time) if i in time_index

for i in cam_index:
    time[i]=now.strftime("%m/%d/%Y, %H:%M:%S")
    
    
#print(time)

with open('/home/pi/Desktop/time.txt','w') as output:
	output.write('\n'.join(map(str, time)))
    

#print("Cameras not connected for longer than 10 minutes: ", time_index)
#print(now)
eligible_cams=set(cam_index)&set(time_index)
if eligible_cams:
    print(now)
    print("Eligible Cameras: ", eligible_cams)
res_new=[x for i,x in enumerate(all_cam) if i in eligible_cams]

res_new=[i.upper() for i in res_new]


with open('/home/pi/Desktop/res_new.txt','w') as output:
	output.write('\n'.join(map(str, res_new)))
	
now=datetime.now()
time=now.strftime("%m/%d/%Y, %H:%M:%S")
with open('/home/pi/Desktop/last_run_time.txt','w') as output:
	output.write(time)
	
eligible_cams=[str(s)+'_'+time for s in eligible_cams]
#print(eligible_cams)
with open('/home/pi/Desktop/all_trigger_events.txt') as f:
	content=f.readlines()
wakeup=[x.strip() for x in content]
#print(wakeup)
wakeup.extend(eligible_cams)
#print(wakeup)
with open('/home/pi/Desktop/all_trigger_events.txt','w') as output:
	output.write('\n'.join(map(str, wakeup)))
