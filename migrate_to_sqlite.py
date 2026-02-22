"""
One-time migration script: JSON files -> SQLite database

Reads each JSON data file, inserts records into the corresponding SQLite table,
and renames the original JSON file to .json.bak after a successful import.

Usage:
    python migrate_to_sqlite.py
"""
import json
import os
import sys
import time
import sqlite3

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_connection import get_db_connection

DATA_DIR = "data"

# Map of JSON files -> (table_name, migration_function)
JSON_FILES = {
    "processed_ids.json": "processed_ids",
    "embeddings_cache.json": "embeddings",
    "message_mapping.json": "message_mapping",
    "vote_tracking.json": "votes",
    "removed_entries.json": "removed_entries",
    "retry_queue.json": "retry_queue",
    "last_message_ids.json": "last_message_ids",
}


def load_json(filepath):
    """Load a JSON file, return None if it doesn't exist or is already backed up."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"  ERROR reading {filepath}: {e}")
        return None


def backup_file(filepath):
    """Rename a JSON file to .json.bak"""
    bak_path = filepath + ".bak"
    if os.path.exists(bak_path):
        # If backup already exists, add timestamp
        bak_path = filepath + f".bak.{int(time.time())}"
    os.rename(filepath, bak_path)
    print(f"  Backed up {filepath} -> {bak_path}")


def migrate_processed_ids(conn, data):
    """Migrate processed_ids.json: {entry_id: timestamp, ...}"""
    count = 0
    for entry_id, timestamp in data.items():
        conn.execute(
            "INSERT OR IGNORE INTO processed_ids (entry_id, timestamp) VALUES (?, ?)",
            (entry_id, timestamp)
        )
        count += 1
    conn.commit()
    return count


def migrate_embeddings(conn, data):
    """Migrate embeddings_cache.json: {content_hash: {embedding, timestamp, preview, content, entry_id}, ...}"""
    count = 0
    for content_hash, entry in data.items():
        embedding = entry.get('embedding')
        if embedding is None:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO embeddings 
               (content_hash, embedding, timestamp, preview, content, entry_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                content_hash,
                json.dumps(embedding),
                entry.get('timestamp', 0),
                entry.get('preview'),
                entry.get('content'),
                entry.get('entry_id')
            )
        )
        count += 1
    conn.commit()
    return count


def migrate_message_mapping(conn, data):
    """Migrate message_mapping.json: {entry_id: {mapping_data}, ...}"""
    count = 0
    for entry_id, mapping in data.items():
        video_urls = mapping.get('video_urls')
        conn.execute(
            """INSERT OR IGNORE INTO message_mapping 
               (entry_id, telegram_message_id, discord_channel_id, discord_message_id,
                content, source_url, video_urls, category, source_type, reasoning, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_id,
                mapping.get('telegram_message_id'),
                mapping.get('discord_channel_id'),
                mapping.get('discord_message_id'),
                mapping.get('content'),
                mapping.get('source_url'),
                json.dumps(video_urls) if video_urls else '[]',
                mapping.get('category'),
                mapping.get('source_type'),
                mapping.get('reasoning'),
                mapping.get('timestamp', time.time())
            )
        )
        count += 1
    conn.commit()
    return count


def migrate_votes(conn, data):
    """Migrate vote_tracking.json: {discord_message_id: {voters, timestamp, ...}, ...}"""
    count = 0
    for discord_message_id, vote_data in data.items():
        voters = vote_data.get('voters', [])
        conn.execute(
            """INSERT OR IGNORE INTO votes 
               (discord_message_id, voters, timestamp, entry_id, content, category, discord_channel_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                discord_message_id,
                json.dumps(voters),
                vote_data.get('timestamp'),
                vote_data.get('entry_id'),
                vote_data.get('content'),
                vote_data.get('category'),
                vote_data.get('discord_channel_id')
            )
        )
        count += 1
    conn.commit()
    return count


def migrate_removed_entries(conn, data):
    """Migrate removed_entries.json: [{entry_id, content, category, ...}, ...]"""
    count = 0
    for entry in data:
        voter_ids = entry.get('voter_ids', [])
        embedding = entry.get('embedding')
        conn.execute(
            """INSERT INTO removed_entries 
               (entry_id, content, category, removed_at, voter_ids,
                discord_message_id, discord_channel_id, source_url, embedding)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.get('entry_id'),
                entry.get('content'),
                entry.get('category'),
                entry.get('removed_at', time.time()),
                json.dumps(voter_ids),
                entry.get('discord_message_id'),
                entry.get('discord_channel_id'),
                entry.get('source_url'),
                json.dumps(embedding) if embedding else None
            )
        )
        count += 1
    conn.commit()
    return count


def migrate_retry_queue(conn, data):
    """Migrate retry_queue.json: {entry_id: {entry, retry_count, ...}, ...}"""
    count = 0
    for entry_id, retry_info in data.items():
        entry_data = retry_info.get('entry', {})
        conn.execute(
            """INSERT OR IGNORE INTO retry_queue 
               (entry_id, entry_data, retry_count, first_attempt_cycle, last_attempt_cycle, reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                entry_id,
                json.dumps(entry_data),
                retry_info.get('retry_count', 1),
                retry_info.get('first_attempt_cycle', 0),
                retry_info.get('last_attempt_cycle', 0),
                retry_info.get('reason', '')
            )
        )
        count += 1
    conn.commit()
    return count


def migrate_last_message_ids(conn, data):
    """Migrate last_message_ids.json: {channel_name: message_id, ...}"""
    count = 0
    for channel_name, message_id in data.items():
        conn.execute(
            "INSERT OR IGNORE INTO last_message_ids (channel_name, message_id) VALUES (?, ?)",
            (channel_name, message_id)
        )
        count += 1
    conn.commit()
    return count


MIGRATORS = {
    "processed_ids.json": migrate_processed_ids,
    "embeddings_cache.json": migrate_embeddings,
    "message_mapping.json": migrate_message_mapping,
    "vote_tracking.json": migrate_votes,
    "removed_entries.json": migrate_removed_entries,
    "retry_queue.json": migrate_retry_queue,
    "last_message_ids.json": migrate_last_message_ids,
}


def main():
    print("=" * 60)
    print("  JSON -> SQLite Migration")
    print("=" * 60)
    print()
    
    conn = get_db_connection()
    
    total_migrated = 0
    summary = []
    
    for json_file, table_name in JSON_FILES.items():
        filepath = os.path.join(DATA_DIR, json_file)
        bak_path = filepath + ".bak"
        
        # Skip if already backed up (migration was already done)
        if not os.path.exists(filepath) and os.path.exists(bak_path):
            print(f"  SKIP {json_file} (already migrated, .bak exists)")
            summary.append((json_file, table_name, "skipped"))
            continue
        
        if not os.path.exists(filepath):
            print(f"  SKIP {json_file} (file not found)")
            summary.append((json_file, table_name, "not found"))
            continue
        
        print(f"  Migrating {json_file} -> {table_name}...")
        
        data = load_json(filepath)
        if data is None:
            summary.append((json_file, table_name, "error reading"))
            continue
        
        migrator = MIGRATORS[json_file]
        try:
            count = migrator(conn, data)
            backup_file(filepath)
            print(f"    OK: {count} records migrated")
            total_migrated += count
            summary.append((json_file, table_name, f"{count} records"))
        except Exception as e:
            print(f"    FAIL: {e}")
            summary.append((json_file, table_name, f"ERROR: {e}"))
    
    print()
    print("=" * 60)
    print("  Migration Summary")
    print("=" * 60)
    print(f"  {'JSON File':<30} {'Table':<20} {'Result'}")
    print(f"  {'-'*30} {'-'*20} {'-'*20}")
    for json_file, table_name, result in summary:
        print(f"  {json_file:<30} {table_name:<20} {result}")
    print()
    print(f"  Total records migrated: {total_migrated}")
    print(f"  Database: {os.path.abspath(os.path.join(DATA_DIR, 'newsbot.db'))}")
    print()
    print("  Original JSON files have been renamed to .json.bak")
    print("  You can safely delete them once you confirm everything works.")
    print()


if __name__ == "__main__":
    main()
