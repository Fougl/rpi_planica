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

---

## SSH in

User is always `pi`, key auth (the desktop's `id_ed25519` is baked into the card),
with a password fallback left enabled on the card.

The Planica PC is Windows 10 and **cannot resolve `.local`** — find the IP:

```
for /L %i in (1,1,254) do @ping -n 1 -w 100 192.168.1.%i >nul
arp -a | findstr /i "b8-27-eb d8-3a-dd e4-5f-01 dc-a6-32 28-cd-c1"
ssh pi@<that ip>
```

Answer the host-key prompt with the full word `yes` — `y` or Enter fails.
Last seen at 192.168.1.108 (DHCP, may move).

---

## Detection logic — read before touching py_new.py

**Two radios, two processes.** The dongle (hci1, USB) scans in its own process.
The onboard radio (hci0, UART) runs gatttool in the main process. Nothing waits
on anything and a write cannot break the scan. Disconnect and reset are scoped
to hci0 — never `systemctl restart bluetooth`, that takes the dongle down.

**The status logic is the original**, byte-identical to the single-radio version:

- A camera fires only on `absent → returned WEAK (< -70) → STRONG (>= -70)`.
- "Absent" means **unseen for 5 minutes**. That is the only re-arm. A camera that
  returns already STRONG does not fire — by design.
- `last_seen` is refreshed by *any* sighting, whatever the RSSI.

**Why the scan floor exists (`SCAN_FLOOR`, default -85, `BT_SCAN_FLOOR` in .env).**
The old Zero's radio went deaf around -85 dBm, so a camera at the top of the
line was genuinely unseen and every trip re-armed it. The dongle hears to -98 —
it still hears the camera at the top, `last_seen` keeps refreshing, the camera
is never absent and can never fire again. 2026-09-03: a full day of riders,
zero detections, sweep still fine. The floor drops sightings fainter than the
old radio could hear *before* the state machine sees them. Same logic, same
ears. Nothing else was changed to make the dongle work.

**What a good trip looks like in the log:**

```
Camera 6 faded below floor (RSSI=-87 < -85) - counting as unseen
Camera 6 is now ABSENT - unseen 5min, last RSSI=-84 - will re-arm on return
Camera 6 was absent >10min, returned WEAK (RSSI=-78)
Camera 6 was WEAK after absence, now STRONG — trigger
🚀 START GATTTOOL FOR CAMERA 6 ... 🥇 SUCCESS
```

**Tuning:** rider goes up, no `ABSENT` line ever appears → the dongle still hears
it at the top → raise the floor (`BT_SCAN_FLOOR=-80`), restart. Parked cameras
read -42 to -62, so there is a lot of room before a floor could make one look
absent. The `faded` line's RSSI is the number to tune against; `bash diag.sh`
gives a full census on both radios and mails it to planica.zipline@gmail.com.

**Known and unchanged:** the 18:00 sweep is a separate cron process that also
writes on hci0. A trigger during the sweep contends for hci0 for a few seconds.
It never touches the scan. Any push restarts the service (deploy.sh) — expect a
startup burst of `returned STRONG` lines after every deploy; that is not a bug.
