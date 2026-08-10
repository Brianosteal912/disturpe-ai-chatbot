#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Python 3 was not found. On Arch Linux, run: sudo pacman -S python"
    exit 1
fi

if [[ ! -x ".venv/bin/python" ]]; then
    echo "Creating virtual environment..."
    "$PYTHON_BIN" -m venv .venv
fi

echo "Installing packages into the project virtual environment..."
".venv/bin/python" -m pip install --upgrade pip setuptools wheel
".venv/bin/python" -m pip install --no-cache-dir -r requirements.txt

".venv/bin/python" -c "import discord; import requests; import dotenv; import sqlite3; c=sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE test USING fts5(content)'); print('Installation test passed - Discord', discord.__version__)"

echo "Installation complete. Start the bot with ./start.sh."
