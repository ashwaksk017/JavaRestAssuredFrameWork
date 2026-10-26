#!/bin/bash
# Start the AI-Integrated QA Workbench and open it in the browser (macOS, Linux).
# Double-click this file in Finder, or run ./start-workbench.command in a
# terminal. Python 3.9+ is found, or installed with Homebrew when there is
# none; tools/agent/launch.py then installs what requirements.txt lists and
# this Python lacks (into the project's .venv when this Python refuses
# pip), and starts the server -- that is where the behaviour is described.
cd "$(dirname "$0")" || exit 1

# A Python that RUNS and is 3.9+ (macOS's /usr/bin/python3 can be a stub
# that only offers to install the developer tools).
find_python() {
    PY=""
    for c in python3 python /opt/homebrew/bin/python3 /usr/local/bin/python3; do
        p="$(command -v "$c" 2>/dev/null)" || continue
        if "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
            PY="$p"
            return 0
        fi
    done
    return 1
}

if ! find_python; then
    echo "Python 3.9 or newer was not found."
    BREW="$(command -v brew || ls /opt/homebrew/bin/brew /usr/local/bin/brew 2>/dev/null | head -1)"
    if [ -n "$BREW" ]; then
        echo "Installing it with Homebrew (a few minutes) ..."
        "$BREW" install python
        find_python
    fi
    if [ -z "$PY" ]; then
        echo "Install Python 3 from https://www.python.org/downloads/ (or install Homebrew, https://brew.sh,"
        echo "and run this again: it installs Python itself)."
        read -r -p "Press Return to close." _
        exit 1
    fi
fi

"$PY" -B tools/agent/launch.py start "$@"
status=$?
if [ $status -ne 0 ]; then
    echo
    echo "The workbench was NOT started. The reason is above."
    read -r -p "Press Return to close." _
fi
exit $status
