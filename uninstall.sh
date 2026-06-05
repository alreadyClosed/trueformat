#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/usr/local/bin"
SCRIPT_NAME="trueformat"
TARGET="$INSTALL_DIR/$SCRIPT_NAME"

if [[ ! -f "$TARGET" ]]; then
    echo "trueformat is not installed at $TARGET"
    exit 0
fi

rm -f "$TARGET"
echo "Removed $TARGET"
