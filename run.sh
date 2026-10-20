#!/usr/bin/env bash
# run.sh
# Startup script for Standalone AI Bill Scanner App

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Detect a Python binary that has Flask installed
PYTHON=""

if [ -x "$DIR/venv/bin/python" ] && "$DIR/venv/bin/python" -c "import flask" 2>/dev/null; then
    PYTHON="$DIR/venv/bin/python"
elif [ -x "$DIR/../employee_crud/venv/bin/python" ] && "$DIR/../employee_crud/venv/bin/python" -c "import flask" 2>/dev/null; then
    PYTHON="$DIR/../employee_crud/venv/bin/python"
elif [ -x "$DIR/../venv/bin/python" ] && "$DIR/../venv/bin/python" -c "import flask" 2>/dev/null; then
    PYTHON="$DIR/../venv/bin/python"
elif command -v python3 >/dev/null 2>&1 && python3 -c "import flask" 2>/dev/null; then
    PYTHON="python3"
elif [ -x "$DIR/venv/bin/python" ]; then
    PYTHON="$DIR/venv/bin/python"
else
    PYTHON="python3"
fi

echo "Using Python: $PYTHON"

# Create .env if missing
if [ ! -f .env ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
fi

echo "Starting AI Bill Scanner Application..."
"$PYTHON" app.py
