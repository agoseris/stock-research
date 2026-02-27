#!/bin/bash
# Start the Streamlit frontend in the background.
# Logs to logs/streamlit.log. PID saved to streamlit.pid.
# Usage: ./start_frontend.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/streamlit.pid"
LOG_FILE="$SCRIPT_DIR/logs/streamlit.log"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Streamlit is already running (PID $(cat "$PID_FILE"))."
    echo "Visit http://localhost:8501"
    exit 0
fi

cd "$FRONTEND_DIR" || exit 1
source venv/bin/activate

nohup streamlit run app.py \
    --server.headless true \
    --server.port 8501 \
    > "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
echo "Streamlit started (PID $(cat "$PID_FILE")) — http://localhost:8501"
echo "Logs: $LOG_FILE"
