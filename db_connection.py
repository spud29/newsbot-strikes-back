"""
Shared SQLite connection manager for the Discord News Aggregator Bot.
All database classes use get_db_connection() to obtain a single shared connection.
"""
import sqlite3
import os
import threading

_connection = None
_db_lock = threading.Lock()


def get_db_lock():
    """Return the lock guarding the shared connection.

    SQLite connections are not safe for concurrent use from multiple
    threads/coroutines even with check_same_thread=False. Every DB op
    must hold this lock, and methods that mutate the in-memory embeddings
    cache must hold it across both the DB op and the cache mutation so
    the cache can't desync from the table.
    """
    return _db_lock


def get_db_connection(db_path=None):
    """
    Get or create the shared SQLite connection.
    
    Returns:
        sqlite3.Connection: Shared database connection with WAL mode enabled
    """
    global _connection
    
    if _connection is not None:
        return _connection
    
    if db_path is None:
        import config
        db_path = config.DB_PATH
    
    # Ensure the data directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    _connection = sqlite3.connect(db_path, check_same_thread=False)
    _connection.row_factory = sqlite3.Row  # Access columns by name
    _connection.execute("PRAGMA journal_mode=WAL")
    _connection.execute("PRAGMA foreign_keys=ON")
    
    _create_tables(_connection)
    _run_migrations(_connection)

    return _connection


def _create_tables(conn):
    """Create all tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS processed_ids (
            entry_id TEXT PRIMARY KEY,
            timestamp REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS embeddings (
            content_hash TEXT PRIMARY KEY,
            embedding TEXT NOT NULL,
            timestamp REAL NOT NULL,
            preview TEXT,
            content TEXT,
            entry_id TEXT
        );

        CREATE TABLE IF NOT EXISTS message_mapping (
            entry_id TEXT PRIMARY KEY,
            telegram_message_id INTEGER,
            discord_channel_id INTEGER,
            discord_message_id INTEGER,
            content TEXT,
            source_url TEXT,
            video_urls TEXT,
            category TEXT,
            source_type TEXT,
            reasoning TEXT,
            timestamp REAL,
            superseded_by TEXT DEFAULT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_message_mapping_discord_msg
            ON message_mapping(discord_message_id);


        CREATE TABLE IF NOT EXISTS removed_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT,
            content TEXT,
            category TEXT,
            removed_at REAL,
            voter_ids TEXT,
            discord_message_id INTEGER,
            discord_channel_id INTEGER,
            source_url TEXT,
            embedding TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_removed_entries_entry_id
            ON removed_entries(entry_id);

        CREATE TABLE IF NOT EXISTS retry_queue (
            entry_id TEXT PRIMARY KEY,
            entry_data TEXT NOT NULL,
            retry_count INTEGER DEFAULT 1,
            first_attempt_cycle INTEGER,
            last_attempt_cycle INTEGER,
            first_attempt_ts REAL,
            last_attempt_ts REAL,
            reason TEXT
        );

        CREATE TABLE IF NOT EXISTS last_message_ids (
            channel_name TEXT PRIMARY KEY,
            message_id INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS entry_reactions (
            entry_id TEXT NOT NULL,
            emoji    TEXT NOT NULL,
            user_ids TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (entry_id, emoji)
        );
    """)
    conn.commit()


def _run_migrations(conn):
    """Run schema migrations for columns added after initial release."""
    # Get existing columns in message_mapping
    columns = {row[1] for row in conn.execute("PRAGMA table_info(message_mapping)").fetchall()}

    if 'user_edited' not in columns:
        conn.execute("ALTER TABLE message_mapping ADD COLUMN user_edited INTEGER DEFAULT 0")
        conn.commit()

    if 'original_category' not in columns:
        conn.execute("ALTER TABLE message_mapping ADD COLUMN original_category TEXT DEFAULT NULL")
        conn.commit()

    if 'placement_reason' not in columns:
        conn.execute("ALTER TABLE message_mapping ADD COLUMN placement_reason TEXT DEFAULT NULL")
        conn.commit()

    if 'superseded_by' not in columns:
        conn.execute("ALTER TABLE message_mapping ADD COLUMN superseded_by TEXT DEFAULT NULL")
        conn.commit()

    if 'superseded_channel_discord_message_id' not in columns:
        conn.execute("ALTER TABLE message_mapping ADD COLUMN superseded_channel_discord_message_id INTEGER DEFAULT NULL")
        conn.commit()

    if 'user_reason' not in columns:
        conn.execute("ALTER TABLE message_mapping ADD COLUMN user_reason TEXT DEFAULT NULL")
        conn.commit()

    if 'secondary_category' not in columns:
        conn.execute("ALTER TABLE message_mapping ADD COLUMN secondary_category TEXT DEFAULT NULL")
        conn.commit()

    if 'original_secondary_category' not in columns:
        conn.execute("ALTER TABLE message_mapping ADD COLUMN original_secondary_category TEXT DEFAULT NULL")
        conn.commit()

    if 'newsworthiness_score' not in columns:
        conn.execute("ALTER TABLE message_mapping ADD COLUMN newsworthiness_score REAL DEFAULT NULL")
        conn.commit()

    # Retry queue moved from restart-fragile cycle counters to wall-clock
    # timestamps. Backfill existing rows with "now" so they become eligible
    # after one normal retry delay instead of sitting frozen.
    rq_columns = {row[1] for row in conn.execute("PRAGMA table_info(retry_queue)").fetchall()}
    if 'first_attempt_ts' not in rq_columns:
        conn.execute("ALTER TABLE retry_queue ADD COLUMN first_attempt_ts REAL")
        conn.execute("ALTER TABLE retry_queue ADD COLUMN last_attempt_ts REAL")
        conn.execute(
            "UPDATE retry_queue SET first_attempt_ts = strftime('%s','now'), "
            "last_attempt_ts = strftime('%s','now')"
        )
        conn.commit()


def close_db_connection():
    """Close the shared connection (call on shutdown)."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
