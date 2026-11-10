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

# PipeWire runs in the user's session, so the service must run as that user.
RUN_USER="${SUDO_USER:-rpi}"
RUN_UID="$(id -u "$RUN_USER")"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rsync -a --delete "$SCRIPT_DIR/" "$APP_DIR/"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=PiAmp audio routing service
After=network-online.target sound.target user@${RUN_UID}.service
Wants=network-online.target user@${RUN_UID}.service

[Service]
Type=simple
WorkingDirectory=/opt/piamp
Environment=XDG_RUNTIME_DIR=/run/user/${RUN_UID}
Environment=PIPEWIRE_RUNTIME_DIR=/run/user/${RUN_UID}
ExecStartPre=/bin/sleep 5
ExecStart=/usr/bin/python3 -m app.main --config /opt/piamp/config.yaml --state /opt/piamp/state/routes.json
Restart=always
RestartSec=5
User=${RUN_USER}

[Install]
WantedBy=multi-user.target
EOF

chown -R "$RUN_USER" "$APP_DIR"
# Start the user manager (and PipeWire) at boot without a login.
loginctl enable-linger "$RUN_USER"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo
echo "Installed PiAmp service to $APP_DIR"
echo "Service file: $SERVICE_FILE"
echo "Logs: journalctl -u $SERVICE_NAME -b"
echo "To check status: sudo systemctl status $SERVICE_NAME"
