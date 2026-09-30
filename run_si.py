#!/usr/bin/env python3
import subprocess, sys, os
os.chdir("/home/brandi/newsbot strikes back")
env = {**os.environ}
env.pop("PYTHONPATH", None)
env.pop("PYTHONHOME", None)
result = subprocess.run([sys.executable, "self_improvement.py"], capture_output=True, text=True, env=env, timeout=120)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[:3000])
print("EXIT:", result.returncode)
