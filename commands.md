# RPi Commands Cheatsheet

## Fresh Pi Setup

Flash **Raspberry Pi OS Lite (32-bit)** with Imager. In its settings: hostname,
username **`pi`** (all paths are /home/pi/...), WiFi + country SI, enable SSH.

Then, on the Pi:

```
git clone https://github.com/Fougl/rpi_planica.git /home/pi/Desktop
bash /home/pi/Desktop/setup.sh
sudo reboot
```

No SSH keys, no tokens - the repo is public and cloned over HTTPS, so this is
identical on every card, every time.

`setup.sh` installs `/etc/bt-clone-addr` from `bt_clone_addr.conf`, which makes
the onboard radio present the original Planica Pi's Bluetooth address. That is
what lets the cameras accept a replacement Pi with no re-pairing. **The reboot
is what applies it** - check with `hciconfig` afterwards.

Only one device using that address may be powered on at a time.

---

## Bluetooth roles

The UB500 dongle scans; the onboard radio does the GATT writes. Adapters are
identified by bus, not index - hci0/hci1 swap between reboots.

```
python3 /home/pi/Desktop/bt_adapters.py     # show the resolved roles
bash /home/pi/Desktop/bt_diag.sh            # full read-only diagnostics
```

Override in `/home/pi/Desktop/.env` if ever needed:

```
BT_SCAN_HCI=1
BT_GATT_HCI=0
```

Swapping those invalidates every camera bond - the bond belongs to whichever
radio does the connecting.

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
