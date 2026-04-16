#!/bin/bash
set -e

VENV_PYTHON=".venv/Scripts/python"

echo "Installing/updating dependencies..."
"$VENV_PYTHON" -m pip install -r requirements.txt > ../pip_out.log 2>&1

echo "Starting newsbot..."
"$VENV_PYTHON" main.py >> ../newsbot_out.log 2>&1
