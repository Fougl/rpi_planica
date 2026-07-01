#!/bin/bash

REPO_DIR="/home/pi/Desktop"
SCRIPT="$REPO_DIR/py_new.py"
DEPLOY_SCRIPT="/home/pi/deploy.sh"
SERVICE_NAME="py_new.service"

echo "=== Setting up py_new service and auto-deploy ==="

# 1. Install Python dependencies
echo "[1/6] Installing Python dependencies..."
sudo apt install -y python3-pip
python3 -c "import bluepy" 2>/dev/null || sudo pip3 install bluepy
python3 -c "import pexpect" 2>/dev/null || sudo pip3 install pexpect
echo "    Python dependencies installed."

# 2. Install systemd service
echo "[2/6] Installing systemd service..."
sudo tee /etc/systemd/system/$SERVICE_NAME > /dev/null <<EOF
[Unit]
Description=Scan Gopro Service
After=network.target bluetooth.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
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

# 3. Create deploy script
echo "[3/6] Creating deploy script..."
cat > $DEPLOY_SCRIPT <<EOF
#!/bin/bash
cd $REPO_DIR || exit 1
git fetch origin master

LOCAL=\$(git rev-parse HEAD)
REMOTE=\$(git rev-parse origin/master)

if [ "\$LOCAL" != "\$REMOTE" ]; then
    git pull origin master
    sudo systemctl restart $SERVICE_NAME
    echo "\$(date): Deployed \$(git rev-parse --short HEAD)" >> /home/pi/deploy.log
fi
EOF
chmod +x $DEPLOY_SCRIPT
echo "    Deploy script created at $DEPLOY_SCRIPT."

# 4. Sudoers entry so pi can restart service and reboot without password
echo "[4/6] Configuring sudoers..."
echo "pi ALL=(ALL) NOPASSWD: /bin/systemctl restart $SERVICE_NAME" | sudo tee /etc/sudoers.d/pi-deploy > /dev/null
echo "pi ALL=(ALL) NOPASSWD: /sbin/reboot" | sudo tee -a /etc/sudoers.d/pi-deploy > /dev/null
echo "    Sudoers entry added."

# 5. Add cron jobs (only if not already there)
echo "[5/6] Setting up cron jobs..."
( sudo -u pi crontab -l 2>/dev/null | grep -v deploy.sh | grep -v monitor_memory; \
  echo "*/2 * * * * /bin/bash $DEPLOY_SCRIPT"; \
  echo "*/5 * * * * /usr/bin/python3 $REPO_DIR/monitor_memory.py" \
) | sudo -u pi crontab -

# End-of-day gatttool sweep goes in ROOT's crontab so gatttool has Bluetooth
# access (same reason the service runs as root). Fires hourly; the script only
# acts at 18:00 Europe/Ljubljana (it checks the timezone itself).
( sudo crontab -l 2>/dev/null | grep -v run_gatttool_all; \
  echo "0 * * * * /usr/bin/python3 $REPO_DIR/run_gatttool_all.py" \
) | sudo crontab -
echo "    Cron jobs set (deploy 2min, memory 5min, 18:00 Ljubljana gatttool sweep)."

# 6. Camera on/off gatttool aliases in ~/.bashrc (idempotent).
echo "[6/6] Adding camera aliases to ~/.bashrc..."
BASHRC="/home/pi/.bashrc"
if ! grep -qF "# === camera gatttool aliases ===" "$BASHRC" 2>/dev/null; then
    cat >> "$BASHRC" <<'EOF'

# === camera gatttool aliases ===
alias cam1on='sudo gatttool -t random -b EF:B5:3D:11:F4:31 --char-write-req -a 0x2f -n 03170101'
alias cam1off='sudo gatttool -t random -b EF:B5:3D:11:F4:31 --char-write-req -a 0x2f -n 03170000'

alias cam2on='sudo gatttool -t random -b D5:ED:26:D6:C2:3B --char-write-req -a 0x2f -n 03170101'
alias cam2off='sudo gatttool -t random -b D5:ED:26:D6:C2:3B --char-write-req -a 0x2f -n 03170000'

alias cam3on='sudo gatttool -t random -b DD:30:F0:C9:83:F0 --char-write-req -a 0x2f -n 03170101'
alias cam3off='sudo gatttool -t random -b DD:30:F0:C9:83:F0 --char-write-req -a 0x2f -n 03170000'

alias cam4on='sudo gatttool -t random -b E7:5C:2C:64:3C:1C --char-write-req -a 0x2f -n 03170101'
alias cam4off='sudo gatttool -t random -b E7:5C:2C:64:3C:1C --char-write-req -a 0x2f -n 03170000'

alias cam5on='sudo gatttool -t random -b FF:30:3A:EB:6B:D3 --char-write-req -a 0x2f -n 03170101'
alias cam5off='sudo gatttool -t random -b FF:30:3A:EB:6B:D3 --char-write-req -a 0x2f -n 03170000'

alias cam6on='sudo gatttool -t random -b EF:BE:79:67:78:46 --char-write-req -a 0x2f -n 03170101'
alias cam6off='sudo gatttool -t random -b EF:BE:79:67:78:46 --char-write-req -a 0x2f -n 03170000'

alias cam7on='sudo gatttool -t random -b F4:8F:F7:98:81:3A --char-write-req -a 0x2f -n 03170101'
alias cam7off='sudo gatttool -t random -b F4:8F:F7:98:81:3A --char-write-req -a 0x2f -n 03170000'

alias cam8on='sudo gatttool -t random -b F3:F6:B0:75:90:61 --char-write-req -a 0x2f -n 03170101'
alias cam8off='sudo gatttool -t random -b F3:F6:B0:75:90:61 --char-write-req -a 0x2f -n 03170000'

alias cam9on='sudo gatttool -t random -b EE:D5:4D:88:77:FF --char-write-req -a 0x2f -n 03170101'
alias cam9off='sudo gatttool -t random -b EE:D5:4D:88:77:FF --char-write-req -a 0x2f -n 03170000'

alias cam10on='sudo gatttool -t random -b EC:0C:E7:74:38:FC --char-write-req -a 0x2f -n 03170101'
alias cam10off='sudo gatttool -t random -b EC:0C:E7:74:38:FC --char-write-req -a 0x2f -n 03170000'

alias cam11on='sudo gatttool -t random -b EE:EA:A6:26:99:7E --char-write-req -a 0x2f -n 03170101'
alias cam11off='sudo gatttool -t random -b EE:EA:A6:26:99:7E --char-write-req -a 0x2f -n 03170000'

alias cam12on='sudo gatttool -t random -b E7:95:BE:D1:C6:61 --char-write-req -a 0x2f -n 03170101'
alias cam12off='sudo gatttool -t random -b E7:95:BE:D1:C6:61 --char-write-req -a 0x2f -n 03170000'

alias cam13on='sudo gatttool -t random -b CC:0B:1A:FD:8B:B6 --char-write-req -a 0x2f -n 03170101'
alias cam13off='sudo gatttool -t random -b CC:0B:1A:FD:8B:B6 --char-write-req -a 0x2f -n 03170000'

alias cam14on='sudo gatttool -t random -b FC:8B:BA:54:66:D1 --char-write-req -a 0x2f -n 03170101'
alias cam14off='sudo gatttool -t random -b FC:8B:BA:54:66:D1 --char-write-req -a 0x2f -n 03170000'
EOF
    echo "    Aliases added to $BASHRC."
else
    echo "    Aliases already present, skipping."
fi


echo ""
echo "=== Done! ==="
echo "Service status:"
sudo systemctl status $SERVICE_NAME --no-pager
