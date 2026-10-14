#!/bin/sh
# Start Holo automatically when you log in (systemd user service).
# Usage: ./install-service.sh [DEVICE_NUMBER]
#   DEVICE_NUMBER is optional: the microphone index from `holo devices`.
set -e
cd "$(dirname "$0")"
DIR="$PWD"
DEVICE="$1"

if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "Run ./install.sh first." >&2
    exit 1
fi
if [ ! -f "${XDG_DATA_HOME:-$HOME/.local/share}/holo/profile.json" ]; then
    echo "No calibration profile yet. Run: holo calibrate" >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found; this installer needs systemd." >&2
    exit 1
fi

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
{
    echo "[Unit]"
    echo "Description=Holo tap-zone listener"
    echo "After=graphical-session.target pipewire.service"
    echo "PartOf=graphical-session.target"
    echo
    echo "[Service]"
    echo "WorkingDirectory=$DIR"
    if [ -n "$DEVICE" ]; then echo "Environment=HOLO_DEVICE=$DEVICE"; fi
    echo "ExecStart=$DIR/.venv/bin/python -u $DIR/holo.py run"
    echo "Restart=on-failure"
    echo "RestartSec=3"
    echo
    echo "[Install]"
    echo "WantedBy=graphical-session.target"
} > "$UNIT_DIR/holo.service"

systemctl --user daemon-reload
systemctl --user enable --now holo.service

echo
echo "Holo now starts when you log in."
echo "  Status:   systemctl --user status holo"
echo "  Logs:     journalctl --user -u holo -f"
echo "  Stop:     systemctl --user stop holo"
echo "  Disable:  systemctl --user disable --now holo"
