#!/usr/bin/bash
set -euo pipefail

SERVICE_NAME="piamp"
APP_DIR="/opt/piamp"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ $EUID -ne 0 ]]; then
  echo "This installer must be run as root."
  echo "Try: sudo ./install_service.sh"
  exit 1
fi

APP_DIR="/opt/piamp"
if [[ ! -d "$APP_DIR" ]]; then
  echo "Installing PiAmp into $APP_DIR"
  mkdir -p "$APP_DIR"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rsync -a --delete "$SCRIPT_DIR/" "$APP_DIR/"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=PiAmp audio routing service
After=network.target sound.target
Wants=network.target sound.target

[Service]
Type=simple
WorkingDirectory=/opt/piamp
ExecStart=/usr/bin/python3 /opt/piamp/app/main.py --config /opt/piamp/config.yaml --state /opt/piamp/state/routes.json
Restart=on-failure
RestartSec=5
User=root
Group=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

echo
echo "Installed PiAmp service to $APP_DIR"
echo "Service file: $SERVICE_FILE"
echo "To start now: sudo systemctl start $SERVICE_NAME"
echo "To check status: sudo systemctl status $SERVICE_NAME"
