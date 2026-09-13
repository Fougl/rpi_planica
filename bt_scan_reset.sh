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
exit 0
