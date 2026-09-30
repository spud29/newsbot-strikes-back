import subprocess, os, time

os.chdir("/home/brandi/newsbot strikes back")
venv_python = ".venv/bin/python"

results = []

# 1. systemctl is-active
r = subprocess.run(["systemctl", "--user", "is-active", "newsbot.service"], capture_output=True, text=True)
results.append("1. systemctl is-active newsbot.service")
results.append(r.stdout.strip() or r.stderr.strip())

# 2. systemctl show NRestarts
r = subprocess.run(["systemctl", "--user", "show", "newsbot.service", "--property=NRestarts"], capture_output=True, text=True)
results.append("")
results.append("2. systemctl show newsbot.service --property=NRestarts")
results.append(r.stdout.strip())

# 3. DB query
r = subprocess.run([venv_python, "-c", """
import time
from database import Database
db = Database()
c = db.conn.cursor()
c.execute('SELECT COUNT(*) FROM message_mapping')
print('total:', c.fetchone()[0])
c.execute('SELECT COUNT(DISTINCT entry_id) FROM message_mapping')
print('unique:', c.fetchone()[0])
c.execute('SELECT COUNT(*) FROM message_mapping WHERE timestamp > ?', (int(time.time()) - 3600,))
print('last_hour:', c.fetchone()[0])
"""], capture_output=True, text=True, cwd="/home/brandi/newsbot strikes back")
results.append("")
results.append("3. DB query")
results.append(r.stdout.strip())
if r.stderr.strip():
    results.append("STDERR: " + r.stderr.strip())

# 4. journalctl errors
r = subprocess.run("journalctl --user -u newsbot.service --since '30min' --no-pager -n 20 2>/dev/null | grep -iE 'error|traceback|crash|exception' || echo 'no errors in last 30min'", shell=True, capture_output=True, text=True)
results.append("")
results.append("4. journalctl errors (last 30min)")
results.append(r.stdout.strip())

output = "\n".join(results)

# Write to file
cache_dir = os.path.expanduser("~/.hermes/cache/newsbot-pulse")
os.makedirs(cache_dir, exist_ok=True)
filepath = os.path.join(cache_dir, "latest-pulse.txt")
with open(filepath, "w") as f:
    f.write(output)
    f.write("\n")

# Determine health
lines = output.split("\n")
is_active = "active" in lines[1].lower() if len(lines) > 1 else False
has_errors = "no errors" not in output.lower()
last_hour = 0
for line in lines:
    if line.startswith("last_hour:"):
        try:
            last_hour = int(line.split(":")[1].strip())
        except:
            pass

needs_attention = (not is_active) or has_errors or (last_hour == 0)
status = "NEEDS ATTENTION" if needs_attention else "healthy"
with open(filepath, "a") as f:
    f.write(status + "\n")

print(output)
print("\n---")
print(f"Status: {status}")
print(f"Saved to: {filepath}")
