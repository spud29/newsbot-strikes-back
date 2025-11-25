"""
Removed Entries Database
Stores entries that were voted as "not valuable" for feedback learning
"""
import json
import os
import time
from utils import logger, ensure_directory


class RemovedEntriesDB:
    """Manages database of entries removed via user votes"""
    
    def __init__(self, db_path="data/removed_entries.json"):
        """
        Initialize removed entries database
        
        Args:
            db_path: Path to removed entries JSON file
        """
        ensure_directory('data')
        self.db_path = db_path
        self.entries = self._load_entries()
        logger.info(f"RemovedEntriesDB initialized with {len(self.entries)} removed entries")
    
    def _load_entries(self):
        """Load removed entries from JSON file"""
        try:
            if os.path.exists(self.db_path):
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return []
        except Exception as e:
            logger.error(f"Error loading removed entries from {self.db_path}: {e}")
            return []
    
    def _save_entries(self):
        """Save removed entries to JSON file"""
        try:
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump(self.entries, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving removed entries to {self.db_path}: {e}")
    
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
        entry = {
            'entry_id': entry_id,
            'content': content,
            'category': category,
            'removed_at': time.time(),
            'voter_ids': voter_ids,
            'discord_message_id': discord_message_id,
            'discord_channel_id': discord_channel_id,
            'source_url': source_url
        }
        
        # Optionally store embedding for similarity checking
        if embedding:
            entry['embedding'] = embedding if isinstance(embedding, list) else embedding.tolist()
        
        self.entries.append(entry)
        self._save_entries()
        
        logger.info(f"Added removed entry: {entry_id} (category: {category}, voters: {len(voter_ids)})")
        
        return entry
    
    def get_recent_removed_entries(self, limit=20):
        """
        Get most recent removed entries (for system prompt examples)
        
        Args:
            limit: Maximum number of entries to return
        
        Returns:
            list: Recent removed entries, sorted by removed_at (newest first)
        """
        # Sort by removed_at timestamp (newest first)
        sorted_entries = sorted(
            self.entries, 
            key=lambda x: x.get('removed_at', 0), 
            reverse=True
        )
        
        return sorted_entries[:limit]
    
    def get_all_removed_entries(self):
        """
        Get all removed entries
        
        Returns:
            list: All removed entries
        """
        return self.entries
    
    def find_by_entry_id(self, entry_id):
        """
        Find a removed entry by entry ID
        
        Args:
            entry_id: Entry ID to search for
        
        Returns:
            dict: Entry data or None if not found
        """
        for entry in self.entries:
            if entry.get('entry_id') == entry_id:
                return entry
        return None
    
    def restore_entry(self, entry_id):
        """
        Restore a removed entry (remove from database)
        Use this if an entry was wrongly marked as not valuable
        
        Args:
            entry_id: Entry ID to restore
        
        Returns:
            bool: True if entry was found and removed, False otherwise
        """
        for i, entry in enumerate(self.entries):
            if entry.get('entry_id') == entry_id:
                removed_entry = self.entries.pop(i)
                self._save_entries()
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
        current_time = time.time()
        cutoff_time = current_time - (max_age_days * 24 * 3600)
        
        # Keep entries newer than cutoff
        old_count = len(self.entries)
        self.entries = [
            entry for entry in self.entries
            if entry.get('removed_at', 0) >= cutoff_time
        ]
        
        removed_count = old_count - len(self.entries)
        
        if removed_count > 0:
            self._save_entries()
            logger.info(f"Cleaned up {removed_count} old removed entries (older than {max_age_days} days)")
        
        return removed_count
    
    def get_stats(self):
        """
        Get statistics about removed entries
        
        Returns:
            dict: Statistics
        """
        total_entries = len(self.entries)
        
        # Count by category
        by_category = {}
        for entry in self.entries:
            category = entry.get('category', 'unknown')
            by_category[category] = by_category.get(category, 0) + 1
        
        # Count recent entries (last 7 days)
        cutoff_time = time.time() - (7 * 24 * 3600)
        recent_count = sum(
            1 for entry in self.entries
            if entry.get('removed_at', 0) >= cutoff_time
        )
        
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
            # Truncate if too long
            if len(content) > max_preview_length:
                preview = content[:max_preview_length] + "..."
            else:
                preview = content
            
            previews.append(preview)
        
        return previews



