#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [[ ! -x ".venv/bin/python" ]]; then
    echo ".venv was not found. Run ./install.sh first."
    exit 1
fi

PID_FILE="$PROJECT_DIR/data/bot.pid"
mkdir -p "$PROJECT_DIR/data"
if [[ -f "$PID_FILE" ]]; then
    EXISTING_PID="$(<"$PID_FILE")"
    if [[ "$EXISTING_PID" =~ ^[0-9]+$ ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
        EXISTING_CWD="$(readlink -f "/proc/$EXISTING_PID/cwd" 2>/dev/null || true)"
        EXISTING_CMD="$(tr '\0' ' ' < "/proc/$EXISTING_PID/cmdline" 2>/dev/null || true)"
        if [[ "$EXISTING_CWD" == "$PROJECT_DIR" && ( "$EXISTING_CMD" == *"bot.py"* || "$EXISTING_CMD" == *"-m app"* ) ]]; then
            echo "The bot is already running (PID: $EXISTING_PID). Use ./stop.sh to stop it."
            exit 0
        fi
    fi
fi

echo "$$" > "$PID_FILE"
echo "Starting Disturpe AI Chatbot... Use ./stop.sh to stop it."
export PYTHONDONTWRITEBYTECODE=1
exec ".venv/bin/python" -m app
