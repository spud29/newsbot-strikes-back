import sqlite3
import sys

db_path = "data/newsbot.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("=" * 80)
print("TABLES IN DATABASE")
print("=" * 80)
rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
for row in rows:
    print(row[0])

print("\n" + "=" * 80)
print("COLUMNS IN message_mapping TABLE")
print("=" * 80)
rows = conn.execute("PRAGMA table_info(message_mapping);").fetchall()
for row in rows:
    cid, name, type_, notnull, dflt, pk = row
    print(f"{name:40} {type_:10} (pk={pk}, notnull={notnull})")

print("\n" + "=" * 80)
print("COLUMNS IN OTHER TABLES")
print("=" * 80)
tables = ['processed_ids', 'embeddings', 'removed_entries', 'retry_queue', 'last_message_ids', 'entry_reactions']
for table in tables:
    print(f"\n{table}:")
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    for row in rows:
        cid, name, type_, notnull, dflt, pk = row
        print(f"  {name:35} {type_:10} (pk={pk})")

print("\n" + "=" * 80)
print("RE-CATEGORIZATION STATS")
print("=" * 80)
rows = conn.execute("""
    SELECT original_category, category, COUNT(*) as count
    FROM message_mapping
    WHERE original_category IS NOT NULL AND original_category != category
    GROUP BY original_category, category
    ORDER BY count DESC
    LIMIT 30
""").fetchall()
print(f"{'FROM':20} {'TO':20} {'COUNT':10}")
print("-" * 50)
for row in rows:
    print(f"{str(row['original_category']):20} {str(row['category']):20} {row['count']:10}")

print("\n" + "=" * 80)
print("CATEGORY DISTRIBUTION")
print("=" * 80)
rows = conn.execute("""
    SELECT category, COUNT(*) as count
    FROM message_mapping
    GROUP BY category
    ORDER BY count DESC
""").fetchall()
print(f"{'CATEGORY':20} {'COUNT':10}")
print("-" * 30)
for row in rows:
    print(f"{str(row['category']):20} {row['count']:10}")

print("\n" + "=" * 80)
print("RECATEGORIZATION TOTALS")
print("=" * 80)
total = conn.execute("SELECT COUNT(*) FROM message_mapping").fetchone()[0]
recategorized = conn.execute("""
    SELECT COUNT(*) FROM message_mapping
    WHERE original_category IS NOT NULL AND original_category != category
""").fetchone()[0]
print(f"Total entries: {total}")
print(f"Re-categorized: {recategorized}")
print(f"Not re-categorized: {total - recategorized}")

print("\n" + "=" * 80)
print("USER EDITS (user_edited flag)")
print("=" * 80)
user_edited = conn.execute("""
    SELECT COUNT(*) FROM message_mapping WHERE user_edited = 1
""").fetchone()[0]
print(f"Entries with user_edited=1: {user_edited}")

conn.close()
