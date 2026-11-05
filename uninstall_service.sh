#!/usr/bin/bash
set -euo pipefail

SERVICE_NAME="piamp"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
APP_DIR="/opt/piamp"

if [[ $EUID -ne 0 ]]; then
  echo "This uninstaller must be run as root."
  echo "Try: sudo ./uninstall_service.sh"
  exit 1
fi

if systemctl list-unit-files | grep -q "${SERVICE_NAME}.service"; then
  echo "Stopping and disabling $SERVICE_NAME"
  systemctl stop "$SERVICE_NAME" || true
  systemctl disable "$SERVICE_NAME" || true
fi

if [[ -f "$SERVICE_FILE" ]]; then
  rm -f "$SERVICE_FILE"
  echo "Removed service file: $SERVICE_FILE"
fi

systemctl daemon-reload

if [[ -d "$APP_DIR" ]]; then
  echo "Removing installed app files from $APP_DIR"
  rm -rf "$APP_DIR"
fi

echo "PiAmp service uninstalled."
