#!/bin/bash
set -euo pipefail

OUTDIR=~/.hermes/cache/newsbot-pulse
OUTFILE="$OUTDIR/latest-pulse.txt"
mkdir -p "$OUTDIR"
: > "$OUTFILE"

# 1. is-active
echo "1. is-active: $(systemctl --user is-active newsbot.service 2>&1)" >> "$OUTFILE"

# 2. NRestarts
echo "2. NRestarts: $(systemctl --user show newsbot.service --property=NRestarts 2>&1)" >> "$OUTFILE"

# 3. DB counts
echo "3. db_counts:" >> "$OUTFILE"
.venv/bin/python -c "
from database import Database
import time
db = Database()
c = db.conn.cursor()
c.execute('SELECT COUNT(*) FROM message_mapping')
print('   total:', c.fetchone()[0])
c.execute('SELECT COUNT(DISTINCT entry_id) FROM message_mapping')
print('   unique:', c.fetchone()[0])
c.execute('SELECT COUNT(*) FROM message_mapping WHERE timestamp > ?', (int(time.time()) - 3600,))
print('   last_hour:', c.fetchone()[0])
" >> "$OUTFILE" 2>&1

# 4. journalctl errors (capture real errors only)
echo "4. journalctl_errors:" >> "$OUTFILE"
JOUT=$(journalctl --user -u newsbot.service --since "30min" --no-pager -n 20 2>/dev/null | grep -iE "\b(error|traceback|crash|exception)\b" || true)
if [ -z "$JOUT" ]; then
  echo "   no errors in last 30min" >> "$OUTFILE"
else
  echo "$JOUT" >> "$OUTFILE"
fi

# Determine status
NEEDS=0
# Check inactive
if grep -q "^1\. is-active: inactive" "$OUTFILE"; then
  NEEDS=1
fi
# Check real error lines (skip the "no errors" sentinel AND section headers)
REAL_ERRORS=$(grep -iE "\b(error|traceback|crash|exception)\b" "$OUTFILE" | grep -v "no errors in last 30min" | grep -v "journalctl_errors:" || true)
if [ -n "$REAL_ERRORS" ]; then
  NEEDS=1
fi
# Check last_hour == 0
LAST_HOUR=$(grep -oP 'last_hour: \K\d+' "$OUTFILE" || echo 0)
if [ "$LAST_HOUR" -eq 0 ]; then
  NEEDS=1
fi

if [ "$NEEDS" -eq 1 ]; then
  echo "NEEDS ATTENTION" >> "$OUTFILE"
else
  echo "healthy" >> "$OUTFILE"
fi

cat "$OUTFILE"
