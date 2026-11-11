#!/usr/bin/bash
set -euo pipefail

# PiAmp needs a real desktop session (PipeWire + a visible terminal), so it is
# started as a desktop autostart app rather than a headless systemd service:
# a system-level service starting at multi-user.target has no reliable access
# to the X session and can never show a terminal window on boot.
SERVICE_NAME="piamp"                  # legacy unit name, only used to clean up old installs
APP_DIR="/opt/piamp"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
AUTOSTART_NAME="piamp.desktop"

if [[ $EUID -ne 0 ]]; then
  echo "This installer must be run as root."
  echo "Try: sudo ./install_service.sh"
  exit 1
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "Installing PiAmp into $APP_DIR"
  mkdir -p "$APP_DIR"
fi

# PipeWire and the terminal UI run in the desktop user's session.
RUN_USER="${SUDO_USER:-rpi}"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
if [[ -z "$RUN_HOME" || ! -d "$RUN_HOME" ]]; then
  echo "Could not resolve a home directory for user '$RUN_USER'."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rsync -a --delete "$SCRIPT_DIR/" "$APP_DIR/"
chmod +x "$APP_DIR/start_manual.sh"
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"

# Remove any previous headless systemd service; it would otherwise start a
# second PiAmp process that fights the autostarted terminal over PipeWire.
if systemctl list-unit-files | grep -q "${SERVICE_NAME}.service"; then
  echo "Removing previous ${SERVICE_NAME}.service"
  systemctl stop "$SERVICE_NAME" || true
  systemctl disable "$SERVICE_NAME" || true
fi
rm -f "$SERVICE_FILE"
systemctl daemon-reload

# Autostart PiAmp in a maximized terminal as soon as the desktop session starts.
AUTOSTART_DIR="$RUN_HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/$AUTOSTART_NAME" <<EOF
[Desktop Entry]
Type=Application
Name=PiAmp
Exec=$APP_DIR/start_manual.sh
X-GNOME-Autostart-enabled=true
EOF
chown -R "$RUN_USER:$RUN_USER" "$AUTOSTART_DIR"

# lxterminal (the terminal emulator used by start_manual.sh on this image) has
# no --maximize flag, so maximizing is done by an Openbox window rule matching
# the "PiAmp" terminal title.
OB_CONFIG_DIR="$RUN_HOME/.config/openbox"
OB_CONFIG_FILE="$OB_CONFIG_DIR/rpd-rc.xml"
mkdir -p "$OB_CONFIG_DIR"
if [[ ! -f "$OB_CONFIG_FILE" && -f /etc/xdg/openbox/rpd-rc.xml ]]; then
  cp /etc/xdg/openbox/rpd-rc.xml "$OB_CONFIG_FILE"
fi
if [[ -f "$OB_CONFIG_FILE" ]] && ! grep -q 'PiAmp:maximize:begin' "$OB_CONFIG_FILE"; then
  sed -i 's#</applications>#  <!-- PiAmp:maximize:begin -->\n  <application title="PiAmp">\n    <maximized>yes</maximized>\n  </application>\n  <!-- PiAmp:maximize:end -->\n</applications>#' "$OB_CONFIG_FILE"
fi
chown -R "$RUN_USER:$RUN_USER" "$OB_CONFIG_DIR"

# Apply the window rule immediately if the desktop is already running, so a
# reboot isn't required to see the effect.
if pgrep -u "$RUN_USER" -x openbox >/dev/null 2>&1; then
  sudo -u "$RUN_USER" DISPLAY=:0 XDG_CONFIG_HOME="$RUN_HOME/.config" openbox --reconfigure || true
fi

# Start the user manager (and PipeWire) at boot even before the desktop logs in.
loginctl enable-linger "$RUN_USER"

echo
echo "Installed PiAmp into $APP_DIR"
echo "Autostart entry: $AUTOSTART_DIR/$AUTOSTART_NAME"
echo "PiAmp will open in a maximized terminal the next time $RUN_USER's desktop starts (e.g. after a reboot)."
echo "To start it immediately without rebooting: sudo -u $RUN_USER DISPLAY=:0 $APP_DIR/start_manual.sh"
