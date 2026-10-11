#!/bin/bash
# Stop the AI Enabled Test WorkBench started from THIS folder (macOS, Linux).
# It ends only a server that says it is this folder's workbench; anything
# else on the port is left alone and named. See tools/agent/launch.py.
cd "$(dirname "$0")" || exit 1

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
    echo "Python 3 was not found, so nothing could be asked or stopped."
    read -r -p "Press Return to close." _
    exit 1
fi

"$PY" -B tools/agent/launch.py stop "$@"
status=$?
if [ $status -ne 0 ]; then
    echo
    read -r -p "Press Return to close." _
fi
exit $status
