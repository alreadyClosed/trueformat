#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/usr/local/bin"
SCRIPT_NAME="trueformat"
SOURCE="$(dirname "$0")/trueformat/trueformat.py"

if [[ ! -f "$SOURCE" ]]; then
    echo "Error: source not found at $SOURCE" >&2
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    echo "Error: python3 is required but not found." >&2
    exit 1
fi

echo "Installing $SCRIPT_NAME to $INSTALL_DIR ..."
install -m 0755 "$SOURCE" "$INSTALL_DIR/$SCRIPT_NAME"
echo "Done. Run:  trueformat --help"
