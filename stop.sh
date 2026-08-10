#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$PROJECT_DIR/data/bot.pid"
LEGACY_PID_FILE="$PROJECT_DIR/.bot.pid"
FOUND=0

stop_if_project_bot() {
    local pid="$1"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 0
    kill -0 "$pid" 2>/dev/null || return 0

    local process_cwd process_cmd
    process_cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
    process_cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    if [[ "$process_cwd" == "$PROJECT_DIR" && ( "$process_cmd" == *"bot.py"* || "$process_cmd" == *"-m app"* ) ]]; then
        kill "$pid"
        echo "Bot stopped (PID: $pid)."
        FOUND=1
    fi
}

if [[ -f "$PID_FILE" ]]; then
    stop_if_project_bot "$(<"$PID_FILE")"
fi
if [[ -f "$LEGACY_PID_FILE" ]]; then
    stop_if_project_bot "$(<"$LEGACY_PID_FILE")"
fi

for process_dir in /proc/[0-9]*; do
    stop_if_project_bot "${process_dir##*/}"
done

rm -f -- "$PID_FILE"
rm -f -- "$LEGACY_PID_FILE"

if [[ "$FOUND" -eq 0 ]]; then
    echo "No running bot process was found for this project."
fi
