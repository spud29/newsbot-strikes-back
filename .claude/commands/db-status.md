Check the current state of the newsbot SQLite database. Run SQL queries against `data/newsbot.db` to report:

1. **Processed entries**: Total count and count from last 24h
2. **Embeddings**: Total stored embeddings
3. **Message mappings**: Total count, breakdown by `source_type` (twitter vs telegram)
4. **Retry queue**: Any entries waiting for retry (show entry_id, retry_count, reason)
5. **Removed entries**: Total voted-out entries
6. **Votes**: Any active votes on messages
7. **Last message IDs**: Per-channel last seen Telegram message IDs

Use `sqlite3` via Bash to query the database. Present results in a clear summary table.
