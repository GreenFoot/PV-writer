#!/bin/sh
# Launch PV Writer from source (macOS / Linux).
cd "$(dirname "$0")/.." || exit 1
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python main.py "$@"
fi
exec python3 main.py "$@"
