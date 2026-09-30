"""One-time migration: consolidate variant category names to canonical.

Safe while the bot runs (uses the sqlite backup API like the clock-skew
repair). Idempotent: rows already canonical are untouched.

Run with: env -u PYTHONPATH -u PYTHONHOME .venv/bin/python migrate_category_variants.py
"""
import sqlite3
import time

from db_connection import get_db_connection
from feedback_profile import canonical_category

BACKUP = f"data/newsbot.db.catfix-{int(time.time())}.bak"

conn = get_db_connection()
cur = conn.cursor()

# Locate the DB file for a file-level backup.
row = cur.execute("PRAGMA database_list").fetchone()
db_path = row[2]
print(f"db file: {db_path}")

# Hot backup via the sqlite backup API (safe with the bot's open connection).
src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
dst = sqlite3.connect(BACKUP)
src.backup(dst)
dst.close()
src.close()
print(f"backup written: {BACKUP}")

cur.execute("SELECT DISTINCT category FROM message_mapping")
rows = cur.fetchall()
changed = 0
for (cat,) in rows:
    canon = canonical_category(cat)
    if canon != cat:
        cur.execute(
            "UPDATE message_mapping SET category=? WHERE category=?",
            (canon, cat),
        )
        changed += cur.rowcount
        print(f"  {cat!r} -> {canon!r} ({cur.rowcount} rows)")

# Also remap original_category values in place per variant.
for (cat,) in rows:
    canon = canonical_category(cat)
    if canon != cat:
        cur.execute(
            "UPDATE message_mapping SET original_category=? WHERE original_category=?",
            (canon, cat),
        )

conn.commit()
cur.execute("SELECT COUNT(DISTINCT category) FROM message_mapping")
print("distinct categories after:", cur.fetchone()[0])
conn.close()
