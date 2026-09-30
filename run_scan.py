#!/usr/bin/env python3
import subprocess
import sys

venv_python = "/home/brandi/newsbot strikes back/.venv/bin/python"
result = subprocess.run([venv_python, "newsbot_monitor.py", "scan"], 
                       capture_output=True, text=True, 
                       cwd="/home/brandi/newsbot strikes back")
print(result.stdout)
print(result.stderr, file=sys.stderr)
sys.exit(result.returncode)
