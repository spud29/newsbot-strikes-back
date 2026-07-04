import sqlite3

db_path = "data/newsbot.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("=" * 80)
print("COLUMNS IN votes TABLE")
print("=" * 80)
rows = conn.execute("PRAGMA table_info(votes);").fetchall()
for row in rows:
    cid, name, type_, notnull, dflt, pk = row
    print(f"  {name:35} {type_:10} (pk={pk})")

print("\nSample from votes table:")
rows = conn.execute("SELECT * FROM votes LIMIT 3;").fetchall()
for row in rows:
    print(dict(row))

print("\n" + "=" * 80)
print("COLUMNS IN dexerto_pending TABLE")
print("=" * 80)
rows = conn.execute("PRAGMA table_info(dexerto_pending);").fetchall()
for row in rows:
    cid, name, type_, notnull, dflt, pk = row
    print(f"  {name:35} {type_:10} (pk={pk})")

print("\nCount in dexerto_pending:", conn.execute("SELECT COUNT(*) FROM dexerto_pending").fetchone()[0])

print("\n" + "=" * 80)
print("ADDITIONAL SCHEMA DETAILS")
print("=" * 80)
print("\nIndexes:")
rows = conn.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index';").fetchall()
for row in rows:
    print(f"  {row['name']:40} on {row['tbl_name']:25} {row['sql'] or 'auto'}")

conn.close()
