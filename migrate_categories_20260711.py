"""
One-time migration for the 2026-07-11 category revamp.

Relabels historical rows so the feedback-learning loops (correction/ignore
examples injected into the hourly prompt) emit only canonical labels:

  technology            -> science & technology   (2026-07-10 rename never migrated data)
  music                 -> pop culture
  fashion               -> pop culture
  software development  -> science & technology
  fitness               -> general news
  politics              -> world news  (content matches geopolitics keywords)
                        -> us politics (otherwise)

Run with the bot STOPPED (NSSM service `NewsBot`):
    Stop-Service NewsBot
    python migrate_categories_20260711.py
    Start-Service NewsBot

A pre-migration copy of the database is written to data/newsbot.db.bak-20260711.
This is a standalone offline script; it deliberately does not use the bot's
db_connection singleton.
"""
import re
import shutil
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "newsbot.db"
BACKUP_PATH = DB_PATH.with_name("newsbot.db.bak-20260711")

# Retired/renamed labels with a single unambiguous destination
STATIC_MAP = {
    "technology": "science & technology",
    "music": "pop culture",
    "fashion": "pop culture",
    "software development": "science & technology",
    "fitness": "general news",
}

# 'politics' splits by content. Same keyword set used for the analysis that
# sized the split (~40% world / ~44% US of politics volume).
WORLD_RX = re.compile(
    r"\b(iran|israel|ukrain|russia|gaza|nato|taiwan|missile|hormuz|houthi|"
    r"ceasefire|putin|zelensk|north korea|south korea|china|chinese|kremlin|"
    r"war\b|military|airstrike|troops|geopolit|diplomat|embassy|sanction|"
    r"treaty|un\s+security|foreign minist)",
    re.IGNORECASE,
)


def split_politics(content: str) -> str:
    return "world news" if content and WORLD_RX.search(content) else "us politics"


def migrate_column(cur, table: str, column: str, content_column: str = "content"):
    """Rewrite one category-bearing column of one table. Returns change counts."""
    changes = {}
    for old, new in STATIC_MAP.items():
        cur.execute(
            f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (new, old)
        )
        if cur.rowcount:
            changes[f"{old} -> {new}"] = cur.rowcount

    # politics needs the row content to decide the destination
    cur.execute(
        f"SELECT rowid, {content_column} FROM {table} WHERE {column} = 'politics'"
    )
    rows = cur.fetchall()
    world = us = 0
    for rowid, content in rows:
        dest = split_politics(content or "")
        cur.execute(
            f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (dest, rowid)
        )
        if dest == "world news":
            world += 1
        else:
            us += 1
    if world:
        changes["politics -> world news"] = world
    if us:
        changes["politics -> us politics"] = us
    return changes


def main():
    if not DB_PATH.exists():
        sys.exit(f"Database not found: {DB_PATH}")
    if BACKUP_PATH.exists():
        sys.exit(f"Backup already exists ({BACKUP_PATH}) — migration already ran? Aborting.")

    shutil.copy2(DB_PATH, BACKUP_PATH)
    print(f"Backup written: {BACKUP_PATH}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    targets = [
        ("message_mapping", "category"),
        ("message_mapping", "original_category"),
        ("message_mapping", "secondary_category"),
        ("removed_entries", "category"),
        ("votes", "category"),
    ]
    for table, column in targets:
        changes = migrate_column(cur, table, column)
        print(f"\n{table}.{column}:")
        if not changes:
            print("  (no rows to migrate)")
        for desc, n in sorted(changes.items()):
            print(f"  {n:6d}  {desc}")

    conn.commit()

    print("\nPost-migration category distribution (message_mapping):")
    cur.execute(
        "SELECT category, COUNT(*) FROM message_mapping GROUP BY category ORDER BY 2 DESC"
    )
    for cat, n in cur.fetchall():
        print(f"  {n:6d}  {cat}")
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
