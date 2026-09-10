# rpi_planica

A Raspberry Pi that arms the GoPro cameras on the Planica zipline over Bluetooth LE,
so nobody has to touch a camera between runs.

This is the **capture half** of a two-part video system at the venue. The delivery
half — 360° processing, stitching, per-customer codes and email delivery — lives in
`pc_planica`.

## How it works

1. **Scan.** A continuous BLE scan looks for the known cameras by MAC address,
   tracking each one's signal strength and how long it has been out of range.
2. **Wait for the return.** A camera that has been away and comes back into range
   becomes eligible again; presence and absence are a small state machine, not a
   single sighting, so a stray advert does not trigger anything.
3. **Fire.** The Pi writes GoPro's shutter command (`03170101`) to the BLE
   characteristic and recording starts. No app, no phone, no operator.
4. **Escalate.** Five failed attempts on one camera sends an email and then stops
   retrying that camera, rather than looping on it.

The service runs under systemd with `Restart=always`, and starts on boot.

## The parts that were earned

Most of this repo is not the happy path. It is what the venue taught it:

**Two radios, by role.** The USB dongle scans; the onboard radio sends the commands.
On a single radio a `gatttool` write stalls the scan, `last_seen` goes stale, and the
camera gets dropped as "not visible" — the trigger silently stops working.

**The onboard radio wears the paired phone's address.** A GoPro only accepts commands
from the radio it bonded with. The Broadcom chip reloads its address from firmware at
every boot, so the clone is reapplied each time — and it must run *before*
`bluetoothd`, which keys its bond store by adapter address and would otherwise read
the wrong directory and see every pairing as missing.

**Adapters are resolved by USB bus, never by index.** `hci0` and `hci1` swap between
reboots depending on enumeration order. Hardcoding an index works until the day it
doesn't, and then every write fails with "Function not implemented (38)".

**A watchdog for a failure that raises nothing.** `bluepy`'s scan can wedge inside a
blocking read: no exception, no log line, and systemd still reports the service
active. A scan takes about 4 seconds, so 120 seconds without one completing means
wedged, not quiet — the process exits non-zero and systemd restarts it with a fresh
`bluepy-helper`. The comment in the code carries the incident that produced it: a
full morning, 2026-09-04 18:11 to 2026-09-05 14:49, with no camera triggered and
nothing at all in the logs.

**Disconnects target one adapter.** Never `systemctl restart bluetooth`, which would
take the scanning dongle down mid-scan.

## Layout

| File | Role |
|---|---|
| `py_new.py` | The service: scanner process, controller, watchdog, retry and email escalation |
| `bt_adapters.py` | Resolves which radio scans and which writes, by bus |
| `bt_clone_addr.sh`, `bt-clone-addr.service`, `bt_clone_addr.conf` | Reapply the cloned adapter address at boot, before `bluetoothd` |
| `cam_write.sh` | Send one GoPro command by hand, on the correct adapter |
| `rssi_watch.py` | Live signal strength for the known cameras, parsed from `btmon` |
| `monitor_memory.py` | Memory watch with email alerts |
| `diag.sh`, `bt_diag.sh` | Bluetooth state dumps for when something is off |
| `setup.sh` | Provisions a fresh Pi: dependencies, service, auto-deploy, sudoers |
| `commands.md` | Operator cheatsheet — fresh setup, logs, service, deploy |

## Configuration

Environment, read from `/home/pi/Desktop/.env` by the systemd unit:

| Variable | Meaning |
|---|---|
| `SMTP_PASS` | App password for the notification mailbox. Never in the repo. |
| `BT_SCAN_HCI` / `BT_GATT_HCI` | Pin the scanning and command radios instead of auto-resolving |
| `BT_SCAN_STALE_AFTER` | Seconds without a completed scan before the watchdog restarts the service (default 120) |

The camera MAC addresses live in `py_new.py` as `KNOWN_CAMERAS`, numbered in order.

## Running it

```bash
./setup.sh                      # fresh Pi: dependencies, service, auto-deploy
sudo systemctl status py_new    # is it alive
journalctl -u py_new -f         # live log
```

Hardware: a Raspberry Pi with its onboard Bluetooth plus a USB BLE dongle, and the
GoPros already bonded to the address the onboard radio clones.
