#!/bin/sh
# Install the Holo GUI: PySide6 plus an application-menu launcher.
# Usage: ./install-gui.sh [PATH_TO_PYTHON]
#   Default python is ./.venv/bin/python (created by ./install.sh).
set -e
cd "$(dirname "$0")"
PY="${1:-$PWD/.venv/bin/python}"
if [ ! -x "$PY" ]; then
    echo "Python not found at $PY. Run ./install.sh first, or pass the path to your venv's python." >&2
    exit 1
fi
if ! "$PY" -c "import PySide6" 2>/dev/null; then
    echo "==> Installing PySide6 (large download, around 200 MB)"
    "$PY" -m pip install -r requirements-gui.txt
fi
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/holo.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Holo
Comment=Tap the desk to trigger actions
Exec="$PY" "$PWD/holo_gui.py"
Terminal=false
Categories=Utility;
DESKTOP
echo
echo "Done. Open Holo from your application menu, or run:"
echo "  $PY $PWD/holo_gui.py"
