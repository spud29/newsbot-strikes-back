import sqlite3

db_path = "data/newsbot.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("=" * 80)
print("RETRY QUEUE STATUS")
print("=" * 80)
retry_count = conn.execute("SELECT COUNT(*) FROM retry_queue").fetchone()[0]
print(f"Entries in retry queue: {retry_count}")
if retry_count > 0:
    rows = conn.execute("""
        SELECT COUNT(*) as count, reason FROM retry_queue GROUP BY reason ORDER BY count DESC
    """).fetchall()
    for row in rows:
        print(f"  {row['reason']:30} {row['count']}")

print("\n" + "=" * 80)
print("SUPERSEDED ENTRIES")
print("=" * 80)
superseded_count = conn.execute("""
    SELECT COUNT(*) FROM message_mapping WHERE superseded_by IS NOT NULL
""").fetchone()[0]
print(f"Entries marked as superseded: {superseded_count}")

print("\n" + "=" * 80)
print("SUMMARY STATISTICS")
print("=" * 80)
total = conn.execute("SELECT COUNT(*) FROM message_mapping").fetchone()[0]
recategorized = conn.execute("""
    SELECT COUNT(*) FROM message_mapping
    WHERE original_category IS NOT NULL AND original_category != category
""").fetchone()[0]
user_edited = conn.execute("""
    SELECT COUNT(*) FROM message_mapping WHERE user_edited = 1
""").fetchone()[0]
placement_reason_set = conn.execute("""
    SELECT COUNT(*) FROM message_mapping WHERE placement_reason IS NOT NULL AND placement_reason != ''
""").fetchone()[0]

print(f"Total entries in message_mapping: {total}")
print(f"Re-categorized entries: {recategorized} ({100*recategorized/total:.1f}%)")
print(f"  (where original_category IS NOT NULL and != category)")
print(f"User-edited entries: {user_edited}")
print(f"Entries with placement_reason: {placement_reason_set}")
print(f"Removed entries (3-vote removal): {conn.execute('SELECT COUNT(*) FROM removed_entries').fetchone()[0]}")
print(f"Reactions recorded: {conn.execute('SELECT COUNT(*) FROM entry_reactions').fetchone()[0]}")
print(f"Votes table records: {conn.execute('SELECT COUNT(*) FROM votes').fetchone()[0]}")
print(f"Dexerto pending: {conn.execute('SELECT COUNT(*) FROM dexerto_pending').fetchone()[0]}")

conn.close()
