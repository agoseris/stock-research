#!/bin/bash
# Stop the Streamlit frontend started by start_frontend.sh.
# Usage: ./stop_frontend.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/streamlit.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found. Is Streamlit running?"
    exit 1
fi

PID=$(cat "$PID_FILE")

if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    rm "$PID_FILE"
    echo "Streamlit stopped (PID $PID)."
else
    echo "Process $PID not found. Cleaning up stale PID file."
    rm "$PID_FILE"
fi
