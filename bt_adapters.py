# -*- coding: utf-8 -*-
"""Decide which Bluetooth adapter scans and which one connects.

The Zero 2 runs two radios: the TP-Link UB500 on USB and the Pi's onboard chip
on the UART. Doing both jobs on one radio is what produced the workarounds in
py_new.py -- a gatttool write had to fight the bluepy scan for the same adapter,
and the failure path answered that by restarting bluetoothd. Giving each job its
own radio removes the contention instead of papering over it:

    USB dongle  ->  BLE scanning   (better sensitivity, hears distant cameras)
    onboard     ->  GATT writes    (gatttool -i hciN)

Adapters are identified by BUS, never by index: hci numbering follows USB
enumeration order at boot and can swap between reboots. Either role can be
pinned in /home/pi/Desktop/.env, which both the service and the cron jobs
already source:

    BT_SCAN_HCI=1
    BT_GATT_HCI=0

Swapping the two values swaps the roles -- worth doing if cameras turn out to be
heard but not reachable, since the write needs a two-way link and the scan does
not.

On a Pi with a single radio (the old Zero at Planica) both roles resolve to that
one adapter, which is exactly the current behaviour.
"""
import os

SYS_BLUETOOTH = "/sys/class/bluetooth"


def adapters():
    """[(index, bus), ...] for every present hciN, lowest index first.

    bus is 'usb' for the dongle, 'uart' for the Pi's onboard radio.
    """
    found = []
    try:
        names = os.listdir(SYS_BLUETOOTH)
    except OSError:
        return found

    for name in names:
        if not name.startswith("hci"):
            continue
        try:
            index = int(name[3:])
        except ValueError:
            continue
        # The device's real sysfs path says how it is attached: a USB dongle
        # sits under .../usb1/1-1/..., the onboard radio under .../serial0/...
        path = os.path.realpath(os.path.join(SYS_BLUETOOTH, name))
        bus = "usb" if "/usb" in path else "uart"
        found.append((index, bus))

    found.sort()
    return found


def _pinned(var):
    """Read BT_SCAN_HCI / BT_GATT_HCI, accepting either '1' or 'hci1'."""
    raw = os.environ.get(var, "").strip()
    if not raw:
        return None
    try:
        return int(raw[3:] if raw.startswith("hci") else raw)
    except ValueError:
        return None


def resolve():
    """Return (scan_hci, gatt_hci) as integers.

    Falls back to the lowest present adapter for a role whose preferred bus is
    absent, so a single-radio Pi keeps working unchanged.
    """
    present = adapters()
    first_of = {}
    for index, bus in present:
        first_of.setdefault(bus, index)

    fallback = present[0][0] if present else 0
    scan = _pinned("BT_SCAN_HCI")
    gatt = _pinned("BT_GATT_HCI")

    if scan is None:
        scan = first_of.get("usb", fallback)
    if gatt is None:
        gatt = first_of.get("uart", fallback)

    return scan, gatt


def describe():
    """One-line summary for the log, so a wrong role assignment is obvious."""
    present = adapters()
    scan, gatt = resolve()
    listing = ", ".join("hci{}={}".format(i, b) for i, b in present) or "none"
    warning = ""
    if scan == gatt:
        warning = "  [!] one adapter doing both jobs"
    return "adapters: {} | scan=hci{} gatt=hci{}{}".format(
        listing, scan, gatt, warning)


if __name__ == "__main__":
    print(describe())
