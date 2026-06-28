# RPi Commands Cheatsheet

## Fresh Pi Setup
```
ssh-keygen -t ed25519
cat ~/.ssh/id_ed25519.pub
```
*(paste the key into GitLab → Settings → SSH Keys)*
```
git clone git@gitlab.com:pogacar.al/desktop_rpi_planica.gi.git /home/pi/Desktop
bash /home/pi/Desktop/setup.sh
```

---

## Service
```
sudo systemctl status py_new.service
sudo systemctl restart py_new.service
sudo systemctl stop py_new.service
sudo systemctl start py_new.service
```

### Live logs
```
journalctl -u py_new.service -f
```

### Log file
```
tail -f /home/pi/Desktop/new_log.log
```

---

## Auto-deploy
```
crontab -l
cat /home/pi/deploy.log
bash /home/pi/deploy.sh
```

### Re-run setup (re-registers cron, service, sudoers)
```
cd /home/pi/Desktop && git pull && bash setup.sh
```

---

## Memory
```
free -h
cat /home/pi/memory_monitor.log
```

### Run memory monitor manually
```
python3 /home/pi/Desktop/monitor_memory.py
```

---

## Git
```
cd /home/pi/Desktop && git log --oneline -5
cd /home/pi/Desktop && git pull
```

---

## Bluetooth
```
sudo systemctl status bluetooth
sudo systemctl restart bluetooth
sudo hcitool lescan --duplicates
```
