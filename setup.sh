#!/bin/bash

REPO_DIR="/home/pi/Desktop"
SCRIPT="$REPO_DIR/py_new.py"
DEPLOY_SCRIPT="$REPO_DIR/deploy.sh"
SERVICE_NAME="py_new.service"

echo "=== Setting up py_new service and auto-deploy ==="

# 1. Install Python dependencies
echo "[1/7] Installing Python dependencies..."
sudo apt install -y python3-pip usbutils rfkill libglib2.0-dev
# Bookworm (Debian 12) enforces PEP 668, so pip refuses to modify the system
# environment without --break-system-packages. Stretch's pip does not know the
# flag at all, so only pass it where it exists -- this script has to keep
# working on the old Zero at Planica as well as on the new one.
PIP_FLAGS=""
if pip3 install --help 2>/dev/null | grep -q -- "--break-system-packages"; then
    PIP_FLAGS="--break-system-packages"
fi
python3 -c "import bluepy" 2>/dev/null || sudo pip3 install $PIP_FLAGS bluepy
python3 -c "import pexpect" 2>/dev/null || sudo pip3 install $PIP_FLAGS pexpect
echo "    Python dependencies installed."

# A freshly plugged USB Bluetooth dongle comes up soft-blocked by rfkill, which
# makes `hciconfig hciN up` fail with "Operation not possible due to RF-kill".
# bluetoothd's AutoEnable only helps once the block is cleared.
echo "    Clearing rfkill blocks..."
sudo rfkill unblock all 2>/dev/null || true
for HCI in /sys/class/bluetooth/hci*; do
    [ -e "$HCI" ] || continue
    sudo hciconfig "$(basename "$HCI")" up 2>/dev/null || true
done
python3 "$REPO_DIR/bt_adapters.py" 2>/dev/null || true

# Boot-time Bluetooth address clone. Inert unless /etc/bt-clone-addr exists.
# This is what lets a replacement Pi present the ORIGINAL Pi's address so the
# cameras accept it with no re-pairing: a camera checks the address it paired
# with, not any key the host holds (verified 2026-08-31 -- wiping every key on
# the host changed nothing, while changing the address broke it immediately).
if [ -f "$REPO_DIR/bt_clone_addr.sh" ]; then
    echo "    Installing bt-clone-addr service..."
    sudo install -m 0755 "$REPO_DIR/bt_clone_addr.sh" /usr/local/sbin/bt-clone-addr.sh
    sudo install -m 0644 "$REPO_DIR/bt-clone-addr.service" /etc/systemd/system/bt-clone-addr.service
    sudo systemctl daemon-reload
    sudo systemctl enable bt-clone-addr.service >/dev/null 2>&1
    if [ -f /etc/bt-clone-addr ]; then
        echo "    Cloning onboard radio to: $(cat /etc/bt-clone-addr | grep -oE '([0-9A-F]{2}:){5}[0-9A-F]{2}' | head -1)"
    elif [ -f "$REPO_DIR/bt_clone_addr.conf" ]; then
        sudo install -m 0644 "$REPO_DIR/bt_clone_addr.conf" /etc/bt-clone-addr
        echo "    Installed /etc/bt-clone-addr: $(grep -oE '([0-9A-F]{2}:){5}[0-9A-F]{2}' /etc/bt-clone-addr | head -1)"
        echo "    Takes effect on next reboot."
    fi
fi

# 2. Prompt once for the Gmail App Password and store it outside git.
echo "[2/7] Configuring email credentials..."
ENV_FILE="$REPO_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    read -s -p "    Enter Gmail App Password for planica.zipline@gmail.com: " SMTP_PASS
    echo
    echo "SMTP_PASS=$SMTP_PASS" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "    Saved to $ENV_FILE."
else
    echo "    $ENV_FILE already exists, skipping prompt."
fi

# 3. Install systemd service
echo "[3/7] Installing systemd service..."
sudo tee /etc/systemd/system/$SERVICE_NAME > /dev/null <<EOF
[Unit]
Description=Scan Gopro Service
After=network.target bluetooth.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
EnvironmentFile=-$ENV_FILE
ExecStart=/usr/bin/python3 $SCRIPT
Restart=always
RestartSec=60
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME
sudo systemctl restart $SERVICE_NAME
echo "    Service installed and started."

# 4. Deploy script now lives in the repo (deploy.sh) so that a push can update
# the deploy logic itself. It used to be generated here into /home/pi/deploy.sh,
# which made it the one file auto-deploy could never update: every change to it
# needed someone to SSH in and re-run this script. Any old generated copy is
# removed so cron cannot keep running it.
echo "[4/7] Using the repo's deploy.sh..."
chmod +x "$REPO_DIR/deploy.sh"
rm -f /home/pi/deploy.sh
DEPLOY_SCRIPT="$REPO_DIR/deploy.sh"
echo "    Deploy script: $DEPLOY_SCRIPT (tracked in git, updates itself)."

# 5. Sudoers entry so pi can restart service and reboot without password
echo "[5/7] Configuring sudoers..."
echo "pi ALL=(ALL) NOPASSWD: /bin/systemctl restart $SERVICE_NAME" | sudo tee /etc/sudoers.d/pi-deploy > /dev/null
echo "pi ALL=(ALL) NOPASSWD: /sbin/reboot" | sudo tee -a /etc/sudoers.d/pi-deploy > /dev/null
echo "    Sudoers entry added."

# 6. Add cron jobs (only if not already there)
echo "[6/7] Setting up cron jobs..."
( sudo -u pi crontab -l 2>/dev/null | grep -v deploy.sh | grep -v monitor_memory; \
  echo "*/2 * * * * /bin/bash $DEPLOY_SCRIPT"; \
  echo "*/5 * * * * set -a; . $ENV_FILE; set +a; /usr/bin/python3 $REPO_DIR/monitor_memory.py" \
) | sudo -u pi crontab -

# End-of-day gatttool sweep goes in ROOT's crontab so gatttool has Bluetooth
# access (same reason the service runs as root). Fires hourly; the script only
# acts at 18:00 Europe/Ljubljana (it checks the timezone itself).
( sudo crontab -l 2>/dev/null | grep -v run_gatttool_all; \
  echo "0 * * * * set -a; . $ENV_FILE; set +a; /usr/bin/python3 $REPO_DIR/run_gatttool_all.py" \
) | sudo crontab -
echo "    Cron jobs set (deploy 2min, memory 5min, 18:00 Ljubljana gatttool sweep)."

# 7. Camera on/off gatttool aliases in ~/.bashrc (idempotent).
echo "[7/7] Adding camera aliases to ~/.bashrc..."
# git does not always carry the executable bit across a Windows checkout.
chmod +x "$REPO_DIR/cam_write.sh" 2>/dev/null || true
BASHRC="/home/pi/.bashrc"
# Rewrite the block every time rather than skipping when present: the aliases
# have been wrong before, and a skip means the fix never reaches an existing Pi.
sed -i '/# === camera gatttool aliases ===/,/^alias cam14off=/d' "$BASHRC" 2>/dev/null
if true; then
    cat >> "$BASHRC" <<EOF

# === camera gatttool aliases ===
alias cam1on='sudo $REPO_DIR/cam_write.sh EF:B5:3D:11:F4:31 03170101'
alias cam1off='sudo $REPO_DIR/cam_write.sh EF:B5:3D:11:F4:31 03170100'

alias cam2on='sudo $REPO_DIR/cam_write.sh D5:ED:26:D6:C2:3B 03170101'
alias cam2off='sudo $REPO_DIR/cam_write.sh D5:ED:26:D6:C2:3B 03170100'

alias cam3on='sudo $REPO_DIR/cam_write.sh DD:30:F0:C9:83:F0 03170101'
alias cam3off='sudo $REPO_DIR/cam_write.sh DD:30:F0:C9:83:F0 03170100'

alias cam4on='sudo $REPO_DIR/cam_write.sh E7:5C:2C:64:3C:1C 03170101'
alias cam4off='sudo $REPO_DIR/cam_write.sh E7:5C:2C:64:3C:1C 03170100'

alias cam5on='sudo $REPO_DIR/cam_write.sh FF:30:3A:EB:6B:D3 03170101'
alias cam5off='sudo $REPO_DIR/cam_write.sh FF:30:3A:EB:6B:D3 03170100'

alias cam6on='sudo $REPO_DIR/cam_write.sh EF:BE:79:67:78:46 03170101'
alias cam6off='sudo $REPO_DIR/cam_write.sh EF:BE:79:67:78:46 03170100'

alias cam7on='sudo $REPO_DIR/cam_write.sh F4:8F:F7:98:81:3A 03170101'
alias cam7off='sudo $REPO_DIR/cam_write.sh F4:8F:F7:98:81:3A 03170100'

alias cam8on='sudo $REPO_DIR/cam_write.sh F3:F6:B0:75:90:61 03170101'
alias cam8off='sudo $REPO_DIR/cam_write.sh F3:F6:B0:75:90:61 03170100'

alias cam9on='sudo $REPO_DIR/cam_write.sh EE:D5:4D:88:77:FF 03170101'
alias cam9off='sudo $REPO_DIR/cam_write.sh EE:D5:4D:88:77:FF 03170100'

alias cam10on='sudo $REPO_DIR/cam_write.sh EC:0C:E7:74:38:FC 03170101'
alias cam10off='sudo $REPO_DIR/cam_write.sh EC:0C:E7:74:38:FC 03170100'

alias cam11on='sudo $REPO_DIR/cam_write.sh EE:EA:A6:26:99:7E 03170101'
alias cam11off='sudo $REPO_DIR/cam_write.sh EE:EA:A6:26:99:7E 03170100'

alias cam12on='sudo $REPO_DIR/cam_write.sh E7:95:BE:D1:C6:61 03170101'
alias cam12off='sudo $REPO_DIR/cam_write.sh E7:95:BE:D1:C6:61 03170100'

alias cam13on='sudo $REPO_DIR/cam_write.sh CC:0B:1A:FD:8B:B6 03170101'
alias cam13off='sudo $REPO_DIR/cam_write.sh CC:0B:1A:FD:8B:B6 03170100'

alias cam14on='sudo $REPO_DIR/cam_write.sh FC:8B:BA:54:66:D1 03170101'
alias cam14off='sudo $REPO_DIR/cam_write.sh FC:8B:BA:54:66:D1 03170100'
EOF
    echo "    Aliases added to $BASHRC."
else
    echo "    Aliases already present, skipping."
fi


echo ""
echo "=== Done! ==="
echo "Service status:"
sudo systemctl status $SERVICE_NAME --no-pager
