#!/bin/bash
# Start the AI Enabled Test WorkBench and open it in the browser (macOS, Linux).
# Double-click this file in Finder, or run ./start-workbench.command in a
# terminal. It does nothing but call tools/agent/launch.py, which is where
# the behaviour is described.
cd "$(dirname "$0")" || exit 1

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
    echo "Python 3 was not found. Install it (https://www.python.org/downloads/ or: brew install python) and run this again."
    read -r -p "Press Return to close." _
    exit 1
fi

"$PY" -B tools/agent/launch.py start "$@"
status=$?
if [ $status -ne 0 ]; then
    echo
    echo "The workbench was NOT started. The reason is above."
    read -r -p "Press Return to close." _
fi
exit $status
