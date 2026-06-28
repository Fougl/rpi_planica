#!/bin/bash
set -e

REPO_DIR="/home/pi/Desktop"
SCRIPT="$REPO_DIR/py_new.py"
DEPLOY_SCRIPT="/home/pi/deploy.sh"
SERVICE_NAME="py_new.service"

echo "=== Setting up py_new service and auto-deploy ==="

# 1. Install Python dependencies
echo "[1/5] Installing Python dependencies..."
sudo apt install -y python3-pip
python3 -c "import bluepy" 2>/dev/null || sudo pip3 install bluepy
python3 -c "import pexpect" 2>/dev/null || sudo pip3 install pexpect
echo "    Python dependencies installed."

# 2. Install systemd service
echo "[2/5] Installing systemd service..."
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
echo "[3/5] Creating deploy script..."
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

# 4. Sudoers entry so pi can restart service without password
echo "[4/5] Configuring sudoers..."
echo "pi ALL=(ALL) NOPASSWD: /bin/systemctl restart $SERVICE_NAME" | sudo tee /etc/sudoers.d/pi-deploy > /dev/null
echo "    Sudoers entry added."

# 5. Add cron job (only if not already there)
echo "[5/5] Setting up cron job..."
( crontab -l 2>/dev/null | grep -v deploy.sh; echo "*/2 * * * * /bin/bash $DEPLOY_SCRIPT" ) | crontab -
echo "    Cron job set (every 2 minutes)."


echo ""
echo "=== Done! ==="
echo "Service status:"
sudo systemctl status $SERVICE_NAME --no-pager
