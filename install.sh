#!/bin/sh
# Holo for Linux installer: works on Arch, Debian/Ubuntu and Fedora.
set -e
cd "$(dirname "$0")"

echo "==> Installing system packages (sudo password may be requested)"
if command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed python portaudio libnotify
elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip libportaudio2 libnotify-bin
elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3 python3-pip portaudio libnotify
else
    echo "Unknown package manager. Please install these yourself, then re-run:"
    echo "  Python 3 (with venv and pip), the PortAudio library, and notify-send"
    exit 1
fi

echo "==> Creating virtual environment and installing Python packages"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "==> Creating the 'holo' command"
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/holo" <<'LAUNCH'
#!/bin/sh
cd "@DIR@" || exit 1
if [ "$#" -eq 0 ]; then set -- run; fi
exec "@DIR@/.venv/bin/python" holo.py "$@"
LAUNCH
sed -i "s|@DIR@|$PWD|g" "$HOME/.local/bin/holo"
chmod +x "$HOME/.local/bin/holo"

echo
echo "Done. Next steps:"
echo "  holo devices      # list microphones"
echo "  holo calibrate    # tap each zone"
echo "  holo eval         # test accuracy"
echo "  holo              # run"
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "NOTE: add \$HOME/.local/bin to your PATH, or log out and back in." ;;
esac
