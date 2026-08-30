#!/bin/bash
# Read-only Bluetooth diagnostics. Changes nothing — safe to run on the live Pi.
# Answers the three questions that decide whether the UB500 works here:
#   1. does the kernel recognise the dongle and load its firmware?
#   2. which hciN is the dongle, and is it hci0 (the one bluepy/gatttool use)?
#   3. is gatttool present at all on this OS?
#
# Usage:  bash bt_diag.sh

sep() { echo; echo "===== $1 ====="; }

sep "MODEL / OS / KERNEL"
cat /proc/device-tree/model 2>/dev/null | tr -d '\0'; echo
grep PRETTY_NAME /etc/os-release
echo "kernel:  $(uname -r)   arch: $(uname -m)"
echo "debian:  $(cat /etc/debian_version 2>/dev/null)"

sep "BLUETOOTH TOOLING"
for t in gatttool hcitool hciconfig bluetoothctl btmon btattach rfkill; do
    p=$(command -v $t)
    printf "%-14s %s\n" "$t" "${p:-MISSING}"
done
echo "bluez pkg:   $(dpkg-query -W -f='${Version}' bluez 2>/dev/null || echo 'not installed')"
echo "bluetoothd:  $(/usr/libexec/bluetooth/bluetoothd -v 2>/dev/null || /usr/lib/bluetooth/bluetoothd -v 2>/dev/null || echo '?')"
echo "firmware-realtek: $(dpkg-query -W -f='${Version}' firmware-realtek 2>/dev/null || echo 'not installed')"

sep "PYTHON DEPS"
python3 -c "import bluepy, os; print('bluepy   OK ->', os.path.dirname(bluepy.__file__))" 2>&1 | head -2
python3 -c "import pexpect; print('pexpect  OK')" 2>&1 | head -2
HELPER=$(python3 -c "import bluepy,os;print(os.path.join(os.path.dirname(bluepy.__file__),'bluepy-helper'))" 2>/dev/null)
if [ -n "$HELPER" ]; then
    if [ -x "$HELPER" ]; then echo "bluepy-helper present and executable"
    else echo "bluepy-helper MISSING OR NOT EXECUTABLE at $HELPER  <-- bluepy scans will fail"; fi
fi

sep "USB DEVICES"
lsusb 2>/dev/null || echo "lsusb missing (apt install usbutils)"
echo "--- Realtek / TP-Link entries ---"
lsusb 2>/dev/null | grep -iE "realtek|tp-link|0bda|2357" || echo "  none found  <-- dongle not enumerated"

sep "HCI ADAPTERS"
hciconfig -a 2>/dev/null || echo "hciconfig missing"
echo "--- which hciN is USB vs onboard UART ---"
for d in /sys/class/bluetooth/hci*; do
    [ -e "$d" ] || continue
    n=$(basename "$d")
    bus=$(readlink -f "$d" | grep -oE "usb[0-9]+|serial[0-9]*|uart" | head -1)
    addr=$(cat "$d/address" 2>/dev/null)
    echo "  $n  bus=${bus:-unknown}  addr=$addr"
done

sep "RFKILL"
rfkill list 2>/dev/null || echo "rfkill missing"

sep "REALTEK FIRMWARE FILES"
ls -la /lib/firmware/rtl_bt/ 2>/dev/null | grep -iE "8761|total" || echo "  no rtl8761 firmware in /lib/firmware/rtl_bt/"

sep "KERNEL BLUETOOTH MESSAGES (last 40)"
sudo dmesg 2>/dev/null | grep -iE "bluetooth|btusb|btrtl|rtl87|hci[0-9]" | tail -40 \
    || echo "  (need sudo, or nothing logged)"

sep "BLUETOOTH SERVICE"
systemctl is-enabled bluetooth 2>/dev/null
systemctl is-active bluetooth 2>/dev/null
systemctl show bluetooth -p ExecStart --value 2>/dev/null

sep "BOOT CONFIG — onboard BT overlay"
for f in /boot/firmware/config.txt /boot/config.txt; do
    [ -f "$f" ] || continue
    echo "--- $f ---"
    grep -nE "disable-bt|miniuart|dtoverlay" "$f" || echo "  (no bt/uart overlays set)"
    break
done
echo "hciuart service: $(systemctl is-enabled hciuart 2>/dev/null || echo 'n/a')"

sep "MEMORY"
free -m | head -2

echo
echo "===== END ====="
