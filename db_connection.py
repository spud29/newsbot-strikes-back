"""
Shared SQLite connection manager for the Discord News Aggregator Bot.
All database classes use get_db_connection() to obtain a single shared connection.
"""
import sqlite3
import os

_connection = None


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
            timestamp REAL
        );
        CREATE INDEX IF NOT EXISTS idx_message_mapping_discord_msg
            ON message_mapping(discord_message_id);

        CREATE TABLE IF NOT EXISTS votes (
            discord_message_id TEXT PRIMARY KEY,
            voters TEXT NOT NULL,
            timestamp REAL,
            entry_id TEXT,
            content TEXT,
            category TEXT,
            discord_channel_id INTEGER
        );

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
            reason TEXT
        );

        CREATE TABLE IF NOT EXISTS last_message_ids (
            channel_name TEXT PRIMARY KEY,
            message_id INTEGER NOT NULL
        );
    """)
    conn.commit()


def close_db_connection():
    """Close the shared connection (call on shutdown)."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
