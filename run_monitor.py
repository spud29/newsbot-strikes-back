#!/usr/bin/env python3
"""Wrapper to run newsbot_monitor.py from cron without shell space issues."""
import subprocess, os, sys

venv_python = "/home/brandi/newsbot strikes back/.venv/bin/python"
workdir = "/home/brandi/newsbot strikes back"
script = os.path.join(workdir, "newsbot_monitor.py")

env = os.environ.copy()
env.pop('PYTHONPATH', None)
env.pop('PYTHONHOME', None)

result = subprocess.run(
    [venv_python, script] + sys.argv[1:],
    cwd=workdir,
    capture_output=True,
    text=True,
    env=env
)

print(result.stdout, end='')
print(result.stderr, end='', file=sys.stderr)
sys.exit(result.returncode)
