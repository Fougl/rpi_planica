#!/bin/bash
# Disable any LE scan left enabled on the SCANNING adapter, by power-cycling it.
#
# Run as ExecStopPost, so it fires every single time py_new stops -- clean exit,
# crash, SIGKILL, watchdog, deploy restart, reboot. That is the point: it cannot
# be skipped, because it is not inside the process that might die badly.
#
# Why this is needed at all. bluepy does not scan through BlueZ. It spawns
# bluepy-helper, which sends raw HCI commands to the controller: "start
# scanning". When that helper is killed, nothing sends "stop scanning", and the
# kernel does not undo raw HCI commands when the socket closes -- it never saw
# them. The controller keeps scanning forever, and every later LE Set Extended
# Scan Enable (opcode 0x2042) comes back Command Disallowed, which the kernel
# logs as "failed: -16". bluepy then asks, is refused, waits its 4 seconds and
# returns an empty list with no exception. The service looks perfectly healthy
# and hears nothing.
#
# systemctl restart is what usually kills it: systemd SIGTERMs every process in
# the cgroup, bluepy-helper included, so a clean-stop handler inside the Python
# scanner races the helper's death and loses. Hence this runs after the service
# is already gone, when there is nothing left to race.
#
# Observed: hci1 deaf from 2026-09-06 to 2026-09-12, six days, cameras
# demonstrably powered on, nothing logged anywhere.
#
# The GATT adapter is never touched -- its bonds and cloned address must survive.

IDX="${BT_SCAN_HCI#hci}"
if [ -z "$IDX" ]; then
    for dev in /sys/class/bluetooth/hci*; do
        [ -e "$dev" ] || continue
        name=$(basename "$dev")
        case "$(readlink -f "$dev")" in
            */usb*) IDX="${name#hci}"; break ;;   # the dongle scans
        esac
    done
fi
# Single-radio Pi: fall back to the only adapter there is.
if [ -z "$IDX" ]; then
    for dev in /sys/class/bluetooth/hci*; do
        [ -e "$dev" ] || continue
        IDX="$(basename "$dev")"; IDX="${IDX#hci}"; break
    done
fi
[ -n "$IDX" ] || exit 0

hciconfig "hci$IDX" down 2>/dev/null
hciconfig "hci$IDX" up   2>/dev/null

# And say so in the journal. Silence here is what made this impossible to check:
# systemd logs an ExecStopPost only when it FAILS, so a guard that works leaves
# no trace at all. On 2026-09-13 the only way to establish that it had ever run
# was to stop the service and read the exec record out of `systemctl show`. One
# line per stop makes "was the adapter cleared?" answerable from
# `journalctl -u py_new` by anyone, at any time, after the fact.
logger -t bt_scan_reset "cleared scan state on hci$IDX (down/up) after py_new stopped"
exit 0
