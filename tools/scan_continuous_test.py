#!/usr/bin/env python3
"""Does ONE continuous scan keep reporting devices, or go quiet after the first?

This is the measurement that decided how py_new scans, kept so it can be re-run
rather than re-argued.

py_new used to call scanner.scan(4), which enables scanning, disables it, and
respawns bluepy-helper on every cycle -- roughly 20,000 teardowns and 20,000
helper processes a day at a 4s cadence, on a controller whose firmware is known
to be fragile. The deaf episodes arrive after hours of that, with no restart
involved. So the scan is now opened once and held open.

Before making that change, one thing had to be measured. BlueZ enables duplicate
filtering on discovery (btmon shows "Filter duplicates: Enabled"). If that filter
is never cleared during a single long scan, each device would be reported ONCE
and then never again -- which looks exactly like a deaf radio to the detector,
and would break camera detection silently. That is the failure mode this repo
keeps being bitten by, so it was measured rather than assumed.

Result on 2026-09-13, hci1, six 30-second windows:

    bucket  1  heard  9 devices  (9 not seen before)
    bucket  2  heard 10 devices  (1 not seen before)
    bucket  3  heard 10 devices  (1 not seen before)
    bucket  4  heard 10 devices  (1 not seen before)
    bucket  5  heard 10 devices  (0 not seen before)
    bucket  6  heard 12 devices  (1 not seen before)

Every window full: the duplicate filter does not suppress repeats, and the
camera logic keeps seeing fresh RSSI every cycle. Three minutes also passed with
no stall, which is a point against bluepy issue 370 (helper stops delivering
after a few minutes) -- though not proof over hours.

Re-run it if the scanning ever looks wrong, or on a replacement dongle. Every
bucket must be non-zero. All the devices arriving in bucket 1 and nothing after
means duplicate filtering IS suppressing repeats on that hardware, and a single
held-open scan cannot drive the camera logic as written -- the fix would then be
raw HCI with filtering explicitly disabled.

Run with py_new stopped, or two scanners fight over the same adapter:

    sudo systemctl stop py_new
    sudo python3 tools/scan_continuous_test.py 1 6 30
    sudo systemctl start py_new
"""
import sys
import time

from bluepy.btle import Scanner

idx = int(sys.argv[1]) if len(sys.argv) > 1 else 1
buckets = int(sys.argv[2]) if len(sys.argv) > 2 else 6
window = int(sys.argv[3]) if len(sys.argv) > 3 else 30

scanner = Scanner(idx)
print("starting ONE continuous scan on hci{} -- {} buckets of {}s".format(
    idx, buckets, window))
sys.stdout.flush()

scanner.start()
started = time.time()
seen_ever = set()

try:
    for bucket in range(1, buckets + 1):
        # Clear only bluepy's own list. The controller's duplicate filter is
        # untouched by this, which is exactly what makes the test meaningful.
        scanner.clear()
        scanner.process(window)
        devices = scanner.getDevices()
        addresses = set(d.addr for d in devices)
        fresh = addresses - seen_ever
        seen_ever |= addresses
        print("bucket {:>2}  t+{:>4.0f}s  heard {:>3} devices  ({} not seen before)".format(
            bucket, time.time() - started, len(addresses), len(fresh)))
        sys.stdout.flush()
finally:
    try:
        scanner.stop()
    except Exception:
        pass

print("")
print("Every bucket should be non-zero. All the devices in bucket 1 and nothing")
print("after means duplicate filtering suppresses repeats on this hardware, and")
print("a single held-open scan cannot drive the camera logic as written.")
