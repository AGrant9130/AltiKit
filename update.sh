#!/bin/sh
# Updates an existing AltiKit checkout: pulls the latest code (if this
# is a git clone) and re-syncs dependencies/Chromium/launcher entry via
# install.sh. Safe to re-run any time.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [ -d .git ]; then
    echo "Pulling latest changes..."
    git pull
else
    echo "This doesn't look like a git clone (probably a downloaded"
    echo "source zip instead) - git pull isn't available here."
    echo "Re-download and extract the latest zip from:"
    echo "  https://github.com/AGrant9130/AltiKit/releases"
    echo "then run install.sh from the new folder."
    echo ""
    echo "Re-syncing dependencies for the current code anyway..."
fi

./install.sh
