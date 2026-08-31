#!/bin/bash
# Send one GoPro command on the GATT adapter -- the radio that holds the camera
# relationships.
#
# The cam*on/cam*off aliases must NOT call gatttool directly. With no -i,
# gatttool lets the kernel choose the source adapter and it picks the USB
# dongle, which no camera has ever paired with, so the write fails with
# "Function not implemented (38)". Hardcoding -i hci0 is no better: the indices
# swap between reboots depending on USB enumeration order (observed). So the
# adapter is resolved by BUS on every call.
#
# Usage: cam_write.sh <MAC> <HEX>       e.g. cam_write.sh FC:8B:BA:54:66:D1 03170101
#   03170101 wifi AP on (wake)   03170100 off
#   03160101 beep on             03160100 off

MAC="$1"
VAL="$2"
if [ -z "$MAC" ] || [ -z "$VAL" ]; then
    echo "usage: $(basename "$0") <MAC> <HEX>" >&2
    exit 2
fi

# BT_GATT_HCI in .env wins, exactly as bt_adapters.py does it.
IDX="${BT_GATT_HCI#hci}"
if [ -z "$IDX" ]; then
    for dev in /sys/class/bluetooth/hci*; do
        [ -e "$dev" ] || continue
        name=$(basename "$dev")
        case "$(readlink -f "$dev")" in
            */usb*) ;;                      # the dongle scans; it never connects
            *) IDX="${name#hci}"; break ;;  # onboard radio holds the bonds
        esac
    done
fi
if [ -z "$IDX" ]; then
    echo "no onboard Bluetooth adapter found" >&2
    exit 1
fi

exec timeout --foreground 10 gatttool -i "hci$IDX" -t random -b "$MAC" \
     --char-write-req -a 0x2f -n "$VAL"
