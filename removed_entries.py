"""
Removed Entries Database
Stores entries that were voted as "not valuable" for feedback learning
"""
import json
import time
from utils import logger
from db_connection import get_db_connection


class RemovedEntriesDB:
    """Manages database of entries removed via user votes"""
    
    def __init__(self):
        """Initialize removed entries database with shared SQLite connection"""
        self.conn = get_db_connection()
        
        count = self.conn.execute("SELECT COUNT(*) FROM removed_entries").fetchone()[0]
        logger.info(f"RemovedEntriesDB initialized with {count} removed entries")
    
    def add_removed_entry(self, entry_id, content, category, voter_ids, 
                         discord_message_id=None, discord_channel_id=None, 
                         source_url=None, embedding=None):
        """
        Add a removed entry to the database
        
        Args:
            entry_id: Unique entry identifier
            content: Full text content
            category: Category it was posted to
            voter_ids: List of Discord user IDs who voted
            discord_message_id: Discord message ID (optional)
            discord_channel_id: Discord channel ID (optional)
            source_url: Original source URL (optional)
            embedding: Embedding vector (optional, for similarity checking)
        
        Returns:
            dict: The stored entry
        """
        embedding_json = None
        if embedding:
            embedding_list = embedding if isinstance(embedding, list) else embedding.tolist()
            embedding_json = json.dumps(embedding_list)
        
        self.conn.execute(
            """INSERT INTO removed_entries 
               (entry_id, content, category, removed_at, voter_ids, 
                discord_message_id, discord_channel_id, source_url, embedding)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_id, content, category, time.time(),
                json.dumps(voter_ids), discord_message_id,
                discord_channel_id, source_url, embedding_json
            )
        )
        self.conn.commit()
        
        logger.info(f"Added removed entry: {entry_id} (category: {category}, voters: {len(voter_ids)})")
        
        return {
            'entry_id': entry_id,
            'content': content,
            'category': category,
            'removed_at': time.time(),
            'voter_ids': voter_ids,
            'discord_message_id': discord_message_id,
            'discord_channel_id': discord_channel_id,
            'source_url': source_url
        }
    
    def get_recent_removed_entries(self, limit=20):
        """
        Get most recent removed entries (for system prompt examples)
        
        Args:
            limit: Maximum number of entries to return
        
        Returns:
            list: Recent removed entries, sorted by removed_at (newest first)
        """
        rows = self.conn.execute(
            "SELECT * FROM removed_entries ORDER BY removed_at DESC LIMIT ?", (limit,)
        ).fetchall()
        
        return [self._row_to_dict(row) for row in rows]
    
    def get_all_removed_entries(self):
        """
        Get all removed entries
        
        Returns:
            list: All removed entries
        """
        rows = self.conn.execute("SELECT * FROM removed_entries ORDER BY removed_at DESC").fetchall()
        return [self._row_to_dict(row) for row in rows]
    
    def find_by_entry_id(self, entry_id):
        """
        Find a removed entry by entry ID
        
        Args:
            entry_id: Entry ID to search for
        
        Returns:
            dict: Entry data or None if not found
        """
        row = self.conn.execute(
            "SELECT * FROM removed_entries WHERE entry_id = ? LIMIT 1", (entry_id,)
        ).fetchone()
        
        return self._row_to_dict(row) if row else None
    
    def is_removed(self, entry_id):
        """
        Check if an entry has been removed
        
        Args:
            entry_id: Entry ID to check
        
        Returns:
            bool: True if entry was removed, False otherwise
        """
        row = self.conn.execute(
            "SELECT 1 FROM removed_entries WHERE entry_id = ? LIMIT 1", (entry_id,)
        ).fetchone()
        return row is not None
    
    def restore_entry(self, entry_id):
        """
        Restore a removed entry (remove from database)
        Use this if an entry was wrongly marked as not valuable
        
        Args:
            entry_id: Entry ID to restore
        
        Returns:
            bool: True if entry was found and removed, False otherwise
        """
        cursor = self.conn.execute(
            "DELETE FROM removed_entries WHERE entry_id = ?", (entry_id,)
        )
        self.conn.commit()
        
        if cursor.rowcount > 0:
            logger.info(f"Restored entry: {entry_id}")
            return True
        
        logger.warning(f"Entry {entry_id} not found in removed entries")
        return False
    
    def cleanup_old_entries(self, max_age_days=90):
        """
        Clean up very old removed entries (keep database size manageable)
        
        Args:
            max_age_days: Maximum age in days to keep
        
        Returns:
            int: Number of entries cleaned up
        """
        cutoff_time = time.time() - (max_age_days * 24 * 3600)
        
        cursor = self.conn.execute(
            "DELETE FROM removed_entries WHERE removed_at < ?", (cutoff_time,)
        )
        self.conn.commit()
        
        deleted = cursor.rowcount
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old removed entries (older than {max_age_days} days)")
        
        return deleted
    
    def get_stats(self):
        """
        Get statistics about removed entries
        
        Returns:
            dict: Statistics
        """
        total_entries = self.conn.execute("SELECT COUNT(*) FROM removed_entries").fetchone()[0]
        
        # Count by category
        rows = self.conn.execute(
            "SELECT category, COUNT(*) as cnt FROM removed_entries GROUP BY category"
        ).fetchall()
        by_category = {row['category'] or 'unknown': row['cnt'] for row in rows}
        
        # Count recent entries (last 7 days)
        cutoff_time = time.time() - (7 * 24 * 3600)
        recent_count = self.conn.execute(
            "SELECT COUNT(*) FROM removed_entries WHERE removed_at >= ?", (cutoff_time,)
        ).fetchone()[0]
        
        return {
            'total_removed_entries': total_entries,
            'removed_last_7_days': recent_count,
            'by_category': by_category
        }
    
    def get_content_previews(self, limit=20, max_preview_length=200):
        """
        Get content previews for recent removed entries (for system prompt)
        
        Args:
            limit: Maximum number of previews to return
            max_preview_length: Maximum length of each preview
        
        Returns:
            list: List of content preview strings
        """
        recent_entries = self.get_recent_removed_entries(limit)
        
        previews = []
        for entry in recent_entries:
            content = entry.get('content', '')
            if len(content) > max_preview_length:
                preview = content[:max_preview_length] + "..."
            else:
                preview = content
            
            previews.append(preview)
        
        return previews
    
    def _row_to_dict(self, row):
        """Convert a SQLite Row to a dict matching the old JSON format"""
        return {
            'entry_id': row['entry_id'],
            'content': row['content'],
            'category': row['category'],
            'removed_at': row['removed_at'],
            'voter_ids': json.loads(row['voter_ids']) if row['voter_ids'] else [],
            'discord_message_id': row['discord_message_id'],
            'discord_channel_id': row['discord_channel_id'],
            'source_url': row['source_url'],
            'embedding': json.loads(row['embedding']) if row['embedding'] else None
        }
