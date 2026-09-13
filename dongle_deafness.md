# The scanning dongle goes deaf — what is known, and what is not

State of play as of 2026-09-13. The UB500 on USB (`hci1`, the scanning radio)
periodically stops hearing anything while reporting itself perfectly healthy.
This file exists so the next person — or the next session — does not have to
rediscover any of it.

**The root cause is NOT proven.** Everything below separates what was measured
from what was inferred, because the inferred part has been wrong twice already.

## The symptom

The controller accepts every command and delivers nothing:

- `Start Discovery` returns `Status: Success`, `Discovering: Enabled`
- `LE Set Extended Scan Enable` returns `Status: Success`
- scan parameters are sane — active scan, accept-all filter policy, 1M + Coded
  PHY, 11.25 ms window at 100% duty cycle
- and **zero** advertising reports arrive, for minutes or days

`py_new` sees `scanner.scan(4)` return an empty list, with no exception. systemd
reports the service `active`. Nothing is logged by anyone. No camera can be
triggered, because no camera is ever heard arriving.

`hciconfig hci1 down` + `up` clears it instantly, every time it has been tried.

## Episodes on record

| When | What |
|---|---|
| 2026-09-04 18:11 → 09-05 14:49 | bluepy wedged in a blocking read (different fault: `scan()` never returned) |
| 2026-09-06 → 09-12 | six days deaf, `heard 0 of 14`, cameras demonstrably powered |
| 2026-09-12 13:42–13:45 | 2520 × `hci1: Opcode 0x2042 failed: -16` during rapid deploy restarts |
| 2026-09-12 15:06–15:16 | deaf 10 min, reset cured it, `37 BLE devices this scan` on recovery |
| 2026-09-13 10:46:02 → 11:35 | **49 min deaf, silent shape, fully measured — see below** |

## The 2026-09-13 episode, measured

This is the best evidence available, so it is worth stating precisely.

- Deafness began at **10:46:02**, the exact second of a manual
  `sudo systemctl restart py_new` from `pts/0`.
- At **10:46:49** `cam_write.sh EF:BE:79:67:78:46 03170101` (`cam6on`) **succeeded
  over `hci0`**. Camera 6 was therefore powered, advertising and connectable.
  **The air was not empty — the radio was deaf.**
- `btmon` over 30 s showed **7 clean scan cycles** (start ≈ 4.03 s scanning,
  ≈ 0.17 s gap) with every command `Success` and **0 advertising reports**.
- `dmesg` showed **no kernel message at all** for this episode. The EBUSY flood
  in the buffer was all from the previous day.
- At **11:35:01/03**, `hciconfig hci1 down` + `up` → **221 advertising reports in
  the next 20 s**, and cameras 11, 7, 6 reported STRONG at 11:35:09.

## What is proven

1. **The fault is stale state inside the controller.** It is not the antenna,
   not RF conditions, not the cameras, not the air, and not the software above
   it — a controller reset cures it while nothing else changes.
2. **The old detector was blind to it.** `latched` was computed as
   `newly_refused` (a rising count of kernel `0x2042 ... -16` refusals) whenever
   `dmesg` was readable. The silent shape produces no kernel message, so
   `latched` stayed false forever: no reset, no mail, service reporting healthy.
   That is why the 49 minutes passed unnoticed, and very likely the six days too.
3. **`ExecStopPost` now runs on every stop**, including crash paths. Evidence:
   exec record `start_time=[12:05:02] … code=exited ; status=0`, plus journal
   lines at 12:08:03 (unattended cron deploy), 14:02:47 (watchdog-triggered
   failure exit), 16:27:26 and 16:31:09.
4. **Killing `bluepy-helper` mid-flight does not reproduce the deafness.** It
   produces a *different* fault: `Scan failed: [Errno 32] Broken pipe` in a tight
   loop, because a `Scanner` whose helper died never spawns another. Fixed
   separately; see `py_new.py`.

## What is NOT proven

**Which stale state wedges the controller.** Three candidates remain, and they
need different cures:

1. **A scan left enabled** by a helper killed mid-scan. The controller keeps
   scanning for a dead owner and reports on delivering nothing.
2. **The duplicate-filter cache wedging.** Scanning runs with
   `Filter duplicates: Enabled`. A controller that never clears that cache
   suppresses every advertiser it has already seen — forever — while reporting
   perfect health.
3. **A firmware/USB fault of the dongle itself**, unrelated to restarts. See the
   external reports below: this dongle is known to go bad "after a day or two"
   on other people's machines with no restart involved.

**Whether restarts are the trigger at all.**
- For: the 10:46 episode began at the exact second of a restart.
- Against: the 11:44 deploy restart, with the *old* unit still installed, did
  **not** latch it. And BlueZ issue 1500 describes the same failure arriving with
  no restart at all.

So the restart correlation may be a trigger, a coincidence, or an accelerant.
One measured instance is not a pattern.

## How the next episode answers it — read this first

`py_new.py` now writes a snapshot the moment an episode begins, **before** the
first reset, because the reset is what destroys the evidence:

```bash
ls -t /home/pi/diag/          # deaf-<timestamp>.txt, one per episode
head -n 8 /home/pi/diag/deaf-*.txt
```

**`urbnum`, sampled twice one second apart, is the measurement that decides it:**

| urbnum | Meaning | Cure |
|---|---|---|
| **climbing**, zero advertisements | USB link alive, **controller latched** | `hciconfig down/up` (already automatic) |
| **frozen** | **USB side is dead** (the "interrupt URBs die" failure reported for RTL8761B) | re-enumeration: unbind/bind, or a physical replug |

Nothing else separates those two, and neither survives a reset. The snapshot also
carries `hciconfig -a`, `btmgmt info`, `lsusb`, a 6 s `btmon` sample and the
`dmesg` tail. It has been exercised against a healthy radio (24 advertisements,
73 KB) — it is known to work, not merely written.

Also useful:

```bash
journalctl -t bt_scan_reset          # every time the adapter was cleared
dmesg -T | grep 0x2042 | tail        # kernel refusals, the other shape
```

## What is in place now

| Mitigation | Where |
|---|---|
| Adapter cleared after every stop, however it stopped | `py_new.service` → `ExecStopPost` → `bt_scan_reset.sh` |
| `bluepy-helper` no longer killed directly, so the scanner's own clean-stop can win the race | `py_new.service` → `KillMode=mixed` |
| Silence alone triggers a reset (60 s), backing off to 15 min | `py_new.py` → `RADIO_SILENT_RESET`, `RADIO_SILENT_RESET_MAX` |
| Kernel-confirmed latch resets fast on a fixed cadence | `py_new.py` → `RADIO_QUIET_RESET` |
| Dead helper rebuilt in ~2 s instead of spinning until the watchdog | `py_new.py`, the `except` in `scanner_loop` |
| Forensic snapshot before the first reset | `py_new.py` → `capture_deaf_snapshot()` |
| Every clear logged to the journal | `bt_scan_reset.sh` → `logger` |
| Unit file reaches the Pi on a push | `deploy.sh` — compares and installs |

These make the fault self-healing and visible. **They do not explain it.**

## External reports — read before theorising

- **BlueZ issue 1500** — TP-Link UB500 on a Raspberry Pi, Bookworm, kernel
  6.12.x, BlueZ 5.66: "after a day or two it enters a bad state and detects only
  BR/EDR (Classic) devices, failing to discover BLE devices." Closed *not
  planned*, **no root cause, no fix**. The reporter wanted a fix that did not
  require a reset or replug and never got one. This is our exact hardware and
  kernel family.
- **RTL8761B extended-scan quirk.** Some RTL8761B/BU dongles claim to support LE
  Extended Scan and then reject it with `-EBUSY` (`0x2042 failed: -16`). A kernel
  RFC detects that on the first rejection and falls back to legacy scan.
- **Do NOT force `HCI_QUIRK_BROKEN_EXT_SCAN` on this dongle.** The quirk was
  deliberately narrowed to USB ID `0bda:a728` because on **`2357:0604` — ours —
  it causes a severe regression**: legacy `LE Set Scan Enable (0x200c)` times out
  and the device re-enumerates in a loop (382 firmware reloads in a single boot
  was measured upstream). Our kernel uses extended scan and it works. Leave it.

## The experiment that would settle it

Reproduce the latch deliberately and capture it:

1. Temporarily install a unit **without** `KillMode=mixed` and **without**
   `ExecStopPost` (that is, the old behaviour).
2. `systemctl restart py_new` in a loop, measuring `btmon` for ~10 s after each.
3. When one comes back with scan-enables > 0 and advertisements = 0, it has
   reproduced — capture `urbnum`, `btmon`, `dmesg` at that moment.
4. Restore the real unit (use a `trap` so it is restored even if the script dies).

This is safe now only because the silence-reset self-heals within ~60 s; worst
case is about a minute of blind cameras. It was attempted on 2026-09-13 and
refused by the operator's tooling as a deliberate degradation of a production
service, so it remains **open**.

A natural episode with the snapshot in place may answer it first, and for free.

## Open questions

1. Latched scan, duplicate-filter cache, or dying USB? (`urbnum` decides.)
2. Are restarts a trigger, or does it also happen with no restart — as issue 1500
   describes? The `bt_scan_reset` journal lines plus snapshot timestamps will
   show whether the next episode followed a stop.
3. Does it correlate with uptime (a day or two, per the external report) rather
   than with events?
4. If `urbnum` proves frozen: implement automatic USB re-enumeration
   (unbind/bind) as an escalation after resets fail. Deliberately not implemented
   yet — an unbind that fails to rebind leaves the dongle gone until someone is
   physically at Planica, and that is a worse failure than the one it fixes.
