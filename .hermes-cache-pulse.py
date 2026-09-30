from database import Database
import time

db = Database()
c = db.conn.cursor()
c.execute("SELECT COUNT(*) FROM message_mapping")
print("total:", c.fetchone()[0])
c.execute("SELECT COUNT(DISTINCT entry_id) FROM message_mapping")
print("unique:", c.fetchone()[0])
c.execute("SELECT COUNT(*) FROM message_mapping WHERE timestamp > ?", (int(time.time()) - 3600,))
print("last_hour:", c.fetchone()[0])
