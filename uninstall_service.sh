#!/usr/bin/bash
set -euo pipefail

SERVICE_NAME="piamp"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
APP_DIR="/opt/piamp"
AUTOSTART_NAME="piamp.desktop"

if [[ $EUID -ne 0 ]]; then
  echo "This uninstaller must be run as root."
  echo "Try: sudo ./uninstall_service.sh"
  exit 1
fi

# Clean up a legacy headless systemd service, if one was installed previously.
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

RUN_USER="${SUDO_USER:-rpi}"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6 || true)"

if [[ -n "$RUN_HOME" && -d "$RUN_HOME" ]]; then
  AUTOSTART_FILE="$RUN_HOME/.config/autostart/$AUTOSTART_NAME"
  if [[ -f "$AUTOSTART_FILE" ]]; then
    rm -f "$AUTOSTART_FILE"
    echo "Removed autostart entry: $AUTOSTART_FILE"
  fi

  OB_CONFIG_FILE="$RUN_HOME/.config/openbox/rpd-rc.xml"
  if [[ -f "$OB_CONFIG_FILE" ]] && grep -q 'PiAmp:maximize:begin' "$OB_CONFIG_FILE"; then
    sed -i '/PiAmp:maximize:begin/,/PiAmp:maximize:end/d' "$OB_CONFIG_FILE"
    echo "Removed PiAmp window rule from: $OB_CONFIG_FILE"
    if pgrep -u "$RUN_USER" -x openbox >/dev/null 2>&1; then
      sudo -u "$RUN_USER" DISPLAY=:0 XDG_CONFIG_HOME="$RUN_HOME/.config" openbox --reconfigure || true
    fi
  fi
fi

if [[ -d "$APP_DIR" ]]; then
  echo "Removing installed app files from $APP_DIR"
  rm -rf "$APP_DIR"
fi

echo "PiAmp service uninstalled."
