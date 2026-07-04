import sqlite3

db_path = "data/newsbot.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("=" * 80)
print("PLACEMENT_REASON EXAMPLES (AI reasoning)")
print("=" * 80)
rows = conn.execute("""
    SELECT entry_id, category, original_category, placement_reason
    FROM message_mapping
    WHERE placement_reason IS NOT NULL AND placement_reason != ''
    LIMIT 5
""").fetchall()
for row in rows:
    print(f"\n{row['entry_id']} ({row['original_category']} -> {row['category']})")
    print(f"  {row['placement_reason']}")

print("\n\n" + "=" * 80)
print("REWRITTEN_CONTENT EXAMPLES (User edits)")
print("=" * 80)
rows = conn.execute("""
    SELECT entry_id, category, content, rewritten_content
    FROM message_mapping
    WHERE rewritten_content IS NOT NULL AND rewritten_content != ''
    LIMIT 3
""").fetchall()
count_rewritten = conn.execute("""
    SELECT COUNT(*) FROM message_mapping WHERE rewritten_content IS NOT NULL AND rewritten_content != ''
""").fetchone()[0]
print(f"\nTotal entries with rewritten_content: {count_rewritten}")
for row in rows:
    print(f"\n{row['entry_id']}")
    print(f"  Original: {row['content'][:100]}...")
    print(f"  Rewritten: {row['rewritten_content'][:100]}...")

print("\n\n" + "=" * 80)
print("REMOVED ENTRIES (User removal votes)")
print("=" * 80)
removed_count = conn.execute("SELECT COUNT(*) FROM removed_entries").fetchone()[0]
print(f"Total removed entries: {removed_count}")
rows = conn.execute("""
    SELECT COUNT(*) as count, category FROM removed_entries GROUP BY category ORDER BY count DESC
""").fetchall()
for row in rows:
    print(f"  {row['category']:20} {row['count']}")

print("\n" + "=" * 80)
print("ENTRY REACTIONS (Emoji votes)")
print("=" * 80)
reactions_count = conn.execute("SELECT COUNT(*) FROM entry_reactions").fetchone()[0]
print(f"Total emoji reactions: {reactions_count}")
rows = conn.execute("""
    SELECT emoji, COUNT(*) as count FROM entry_reactions GROUP BY emoji ORDER BY count DESC
""").fetchall()
print("\nTop emoji reactions:")
for row in rows:
    print(f"  {row['emoji']:10} {row['count']}")

print("\n" + "=" * 80)
print("RETRY QUEUE")
print("=" * 80)
retry_count = conn.execute("SELECT COUNT(*) FROM retry_queue").fetchone()[0]
print(f"Entries in retry queue: {retry_count}")
if retry_count > 0:
    rows = conn.execute("""
        SELECT COUNT(*) as count, reason FROM retry_queue GROUP BY reason ORDER BY count DESC
    """).fetchall()
    for row in rows:
        print(f"  {row['reason']:30} {row['count']}")

conn.close()
