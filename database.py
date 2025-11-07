"""
JSON Database management for the Discord News Aggregator Bot
"""
import json
import os
import time
import hashlib
import numpy as np
from datetime import datetime, timedelta
from utils import logger, ensure_directory
import config

class Database:
    """Manages JSON databases for processed IDs and embeddings cache"""
    
    def __init__(self):
        """Initialize database with empty dicts if files don't exist"""
        ensure_directory('data')
        
        self.processed_ids_path = config.DB_PROCESSED_IDS
        self.embeddings_path = config.DB_EMBEDDINGS
        self.message_mapping_path = "data/message_mapping.json"
        
        self.processed_ids = self._load_json(self.processed_ids_path, {})
        self.embeddings = self._load_json(self.embeddings_path, {})
        self.message_mapping = self._load_json(self.message_mapping_path, {})
        
        logger.info(f"Database initialized: {len(self.processed_ids)} processed IDs, {len(self.embeddings)} embeddings, {len(self.message_mapping)} message mappings")
    
    def _load_json(self, filepath, default=None):
        """Load JSON file, return default if it doesn't exist"""
        try:
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return default if default is not None else {}
        except Exception as e:
            logger.error(f"Error loading {filepath}: {e}")
            return default if default is not None else {}
    
    def _save_json(self, filepath, data):
        """Save data to JSON file"""
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving {filepath}: {e}")
    
    def is_processed(self, entry_id):
        """
        Check if an entry ID has been processed
        
        Args:
            entry_id: Unique identifier (e.g., 'twitter_123456' or 'telegram_789')
        
        Returns:
            bool: True if already processed
        """
        return entry_id in self.processed_ids
    
    def mark_processed(self, entry_id):
        """
        Mark an entry as processed with current timestamp
        
        Args:
            entry_id: Unique identifier to mark as processed
        """
        self.processed_ids[entry_id] = time.time()
        self._save_json(self.processed_ids_path, self.processed_ids)
        logger.debug(f"Marked as processed: {entry_id}")
    
    def add_embedding(self, content, embedding):
        """
        Store an embedding for duplicate detection
        
        Args:
            content: Text content to hash for unique ID
            embedding: List/array of embedding values
        
        Returns:
            str: Hash key for the stored embedding
        """
        # Create hash of content for unique ID
        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
        
        self.embeddings[content_hash] = {
            'embedding': embedding if isinstance(embedding, list) else embedding.tolist(),
            'timestamp': time.time(),
            'preview': content[:100]  # Store preview for debugging
        }
        
        self._save_json(self.embeddings_path, self.embeddings)
        logger.debug(f"Stored embedding for: {content[:50]}...")
        
        return content_hash
    
    def find_similar(self, embedding, threshold=config.DUPLICATE_THRESHOLD):
        """
        Find similar embeddings above threshold using cosine similarity
        
        Args:
            embedding: Embedding vector to compare
            threshold: Similarity threshold (0.0-1.0)
        
        Returns:
            tuple: (is_duplicate, similarity_score, matching_preview) or (False, 0.0, None)
        """
        if not self.embeddings:
            return False, 0.0, None
        
        embedding_array = np.array(embedding)
        
        for hash_key, data in self.embeddings.items():
            stored_embedding = np.array(data['embedding'])
            similarity = self._cosine_similarity(embedding_array, stored_embedding)
            
            if similarity >= threshold:
                logger.info(f"Duplicate detected! Similarity: {similarity:.3f} - {data['preview']}")
                return True, similarity, data['preview']
        
        return False, 0.0, None
    
    def _cosine_similarity(self, vec1, vec2):
        """
        Calculate cosine similarity between two vectors
        
        Args:
            vec1: First vector
            vec2: Second vector
        
        Returns:
            float: Cosine similarity (0.0-1.0)
        """
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)
    
    def cleanup_old_entries(self):
        """Remove entries older than DB_RETENTION_HOURS"""
        current_time = time.time()
        cutoff_time = current_time - (config.DB_RETENTION_HOURS * 3600)
        
        # Clean processed IDs
        old_ids = [
            entry_id for entry_id, timestamp in self.processed_ids.items()
            if timestamp < cutoff_time
        ]
        
        for entry_id in old_ids:
            del self.processed_ids[entry_id]
        
        if old_ids:
            self._save_json(self.processed_ids_path, self.processed_ids)
            logger.info(f"Cleaned up {len(old_ids)} old processed IDs")
        
        # Clean embeddings
        old_embeddings = [
            hash_key for hash_key, data in self.embeddings.items()
            if data['timestamp'] < cutoff_time
        ]
        
        for hash_key in old_embeddings:
            del self.embeddings[hash_key]
        
        if old_embeddings:
            self._save_json(self.embeddings_path, self.embeddings)
            logger.info(f"Cleaned up {len(old_embeddings)} old embeddings")
    
    def get_stats(self):
        """
        Get database statistics
        
        Returns:
            dict: Statistics about the database
        """
        return {
            'processed_ids': len(self.processed_ids),
            'embeddings': len(self.embeddings),
            'message_mappings': len(self.message_mapping)
        }
    
    def store_message_mapping(self, telegram_entry_id, telegram_message_id, discord_channel_id, discord_message_id, content=None):
        """
        Store mapping between Telegram and Discord messages
        
        Args:
            telegram_entry_id: Full entry ID (e.g., 'telegram_channelname_123')
            telegram_message_id: Numeric Telegram message ID
            discord_channel_id: Discord channel ID where message was posted
            discord_message_id: Discord message ID
            content: The message content (for edit comparison)
        """
        mapping_key = telegram_entry_id
        self.message_mapping[mapping_key] = {
            'telegram_message_id': telegram_message_id,
            'discord_channel_id': discord_channel_id,
            'discord_message_id': discord_message_id,
            'content': content,
            'timestamp': time.time()
        }
        self._save_json(self.message_mapping_path, self.message_mapping)
        logger.debug(f"Stored message mapping: {telegram_entry_id} -> Discord {discord_message_id}")
    
    def get_discord_message_info(self, telegram_entry_id):
        """
        Get Discord message info for a Telegram entry
        
        Args:
            telegram_entry_id: Full entry ID (e.g., 'telegram_channelname_123')
        
        Returns:
            dict: Discord message info or None if not found
        """
        return self.message_mapping.get(telegram_entry_id)

