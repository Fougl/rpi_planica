#!/bin/bash
# Reapply a cloned Bluetooth address to the ONBOARD radio at every boot.
#
# The Broadcom chip reloads its address from firmware on each boot, so
# `btmgmt public-addr` is a runtime-only change and must be redone. This must
# run BEFORE bluetoothd, because bluetoothd keys its bond store by adapter
# address -- start it first and it reads /var/lib/bluetooth/<wrong-address>/
# and every bond looks missing.
#
# The address is read from /etc/bt-clone-addr. No file, no action -- so this is
# inert until someone deliberately populates it.
CONF=/etc/bt-clone-addr
[ -r "$CONF" ] || exit 0
ADDR=$(grep -oE "([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}" "$CONF" | head -1)
[ -n "$ADDR" ] || exit 0

# Find the onboard (UART) adapter by bus. Indices are NOT stable across
# reboots -- observed swapping when the dongle enumerated first.
IDX=""
for d in /sys/class/bluetooth/hci*; do
    [ -e "$d" ] || continue
    n=$(basename "$d")
    case "$(readlink -f "$d")" in
        */usb*) ;;
        *) IDX="${n#hci}"; break ;;
    esac
done
[ -n "$IDX" ] || { echo "bt-clone-addr: no onboard adapter found"; exit 0; }

CUR=$(hciconfig "hci$IDX" 2>/dev/null | grep -oE "([0-9A-F]{2}:){5}[0-9A-F]{2}" | head -1)
if [ "${CUR^^}" = "${ADDR^^}" ]; then
    echo "bt-clone-addr: hci$IDX already $ADDR"
    exit 0
fi

echo "bt-clone-addr: hci$IDX $CUR -> $ADDR"
btmgmt --index "$IDX" power off       >/dev/null 2>&1
btmgmt --index "$IDX" public-addr "$ADDR" >/dev/null 2>&1
btmgmt --index "$IDX" power on        >/dev/null 2>&1
sleep 1
hciconfig "hci$IDX" | grep "BD Address"
