#!/usr/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CMD="cd \"$SCRIPT_DIR\" && /usr/bin/python3 -m app.main --config config.yaml --state state/routes.json"
PAUSE_CMD="echo; echo 'Press Enter to close this window...'; read -r -p ''"

TERMINAL=""
for candidate in x-terminal-emulator gnome-terminal konsole xfce4-terminal mate-terminal lxterminal terminator uxterm xterm; do
  if command -v "$candidate" >/dev/null 2>&1; then
    TERMINAL="$candidate"
    break
  fi
done

if [[ -n "$TERMINAL" ]]; then
  echo "Opening PiAmp in a visible terminal..."
  TERMINAL_NAME="$(basename "$(readlink -f "$(command -v "$TERMINAL")")")"
  case "$TERMINAL_NAME" in
    gnome-terminal)
      exec "$TERMINAL" --maximize -- bash -lc "$CMD; $PAUSE_CMD"
      ;;
    konsole)
      exec "$TERMINAL" --fullscreen --noclose -e bash -lc "$CMD; $PAUSE_CMD"
      ;;
    xfce4-terminal|mate-terminal)
      exec "$TERMINAL" --maximize -e "bash -lc '$CMD; $PAUSE_CMD'"
      ;;
    lxterminal)
      # Maximized by a labwc windowRule matching this title (~/.config/labwc/rc.xml).
      exec "$TERMINAL" --title=PiAmp -e bash -lc "$CMD; $PAUSE_CMD"
      ;;
    xterm|uxterm)
      exec "$TERMINAL" -maximized -e bash -lc "$CMD; $PAUSE_CMD"
      ;;
    *)
      exec "$TERMINAL" -e bash -lc "$CMD; $PAUSE_CMD"
      ;;
  esac
fi

echo "No graphical terminal emulator was found; starting in the current terminal instead."
exec bash -lc "$CMD; $PAUSE_CMD"
