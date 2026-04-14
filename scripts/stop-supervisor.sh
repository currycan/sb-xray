#!/bin/bash

# Check if the supervisord PID file exists
if [ -f "/run/supervisord.pid" ]; then
    PID=$(cat "/run/supervisord.pid")
    echo "Stopping supervisord with PID: $PID"
    kill -3 "$PID"
    # Optional: wait for the process to terminate
    sleep 2
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "supervisord stopped successfully."
    else
        echo "Failed to stop supervisord, attempting SIGKILL."
        kill -9 "$PID"
    fi
else
    echo "supervisord PID file not found. Is supervisord running?"
fi
