#!/bin/sh
# Launcher for the VW3A1111/VW3A1121 Language File Updater.
# Resolves paths relative to this script's own location so it works
# regardless of what directory it's launched from (e.g. an app launcher).
# Output is also logged to a file since app launchers don't show a
# terminal - if it crashes on startup, check that log for the traceback.
DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$HOME/.vw3a_lang_updater"
mkdir -p "$LOG_DIR"
exec "$DIR/venv/bin/python3" "$DIR/vw3a_lang_updater.py" >>"$LOG_DIR/launch.log" 2>&1
