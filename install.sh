#!/bin/sh
# One-time setup for AltiKit on Linux/macOS: creates the venv, installs
# dependencies and Playwright's Chromium, and (Linux only) registers an
# app-launcher entry. Safe to re-run any time - every step is
# idempotent, so this also doubles as the "sync dependencies" half of
# update.sh.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [ ! -d venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Installing/updating dependencies..."
"$DIR/venv/bin/pip" install --upgrade pip -q
"$DIR/venv/bin/pip" install -r requirements.txt -q

echo "Ensuring Playwright's Chromium is installed (skips this if already present)..."
"$DIR/venv/bin/python3" -m playwright install chromium

if [ "$(uname)" = "Linux" ]; then
    DESKTOP_DIR="$HOME/.local/share/applications"
    mkdir -p "$DESKTOP_DIR"
    cat > "$DESKTOP_DIR/altikit.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=AltiKit
Comment=Update language files, and export configs/screenshots, on a Schneider Electric VW3A1111/VW3A1121 keypad
Exec=$DIR/launch.sh
Icon=$DIR/packaging/icon/icon.svg
Terminal=false
Categories=Utility;
EOF
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" 2>/dev/null
    echo "Added an app-launcher entry (AltiKit) - your launcher may need"
    echo "a restart/logout to pick it up."
fi

echo ""
echo "Setup complete. Launch with: $DIR/launch.sh"
