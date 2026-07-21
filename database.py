"""
SQLite Database management for the Discord News Aggregator Bot
"""
import json
import time
import hashlib
import numpy as np
from utils import logger
from db_connection import get_db_connection, get_db_lock


class Database:
    """Manages SQLite tables for processed IDs, embeddings cache, and message mappings"""
    
    def __init__(self):
        """Initialize database connection and load embeddings cache into memory"""
        self.conn = get_db_connection()
        
        # Load embeddings into memory for fast cosine similarity lookups
        # (SQLite can't do vector math natively)
        self._embeddings_cache = {}
        self._load_embeddings_cache()
        
        # Stats for logging
        processed_count = self.conn.execute("SELECT COUNT(*) FROM processed_ids").fetchone()[0]
        embeddings_count = len(self._embeddings_cache)
        mapping_count = self.conn.execute("SELECT COUNT(*) FROM message_mapping").fetchone()[0]
        
        logger.info(f"Database initialized: {processed_count} processed IDs, {embeddings_count} embeddings, {mapping_count} message mappings")
    
    def _load_embeddings_cache(self):
        """Load all embeddings from SQLite into memory for fast similarity search.
        Pre-converts to numpy arrays and pre-computes L2 norms to avoid
        repeated conversion and norm calculation during find_best_match().

        CACHE CONCURRENCY CONTRACT: _embeddings_cache is copy-on-write. Writers
        (which already serialize on get_db_lock) build a NEW dict and swap the
        reference in one atomic assignment; a published dict is never mutated.
        Readers (find_best_match/find_top_matches, called on the event loop)
        grab the current reference and iterate it without any lock — in-place
        mutation here used to race them into "dictionary changed size during
        iteration"."""
        with get_db_lock():
            rows = self.conn.execute(
                "SELECT content_hash, embedding, timestamp, preview, content, entry_id FROM embeddings"
            ).fetchall()

            cache = {}
            for row in rows:
                embedding_list = json.loads(row['embedding'])
                embedding_np = np.array(embedding_list)
                norm = np.linalg.norm(embedding_np)
                cache[row['content_hash']] = {
                    'embedding': embedding_list,
                    'embedding_np': embedding_np,
                    'norm': norm,
                    'timestamp': row['timestamp'],
                    'preview': row['preview'],
                    'content': row['content'],
                    'entry_id': row['entry_id']
                }
            self._embeddings_cache = cache
    
    def is_processed(self, entry_id):
        """
        Check if an entry ID has been processed
        
        Args:
            entry_id: Unique identifier (e.g., 'twitter_123456' or 'telegram_789')
        
        Returns:
            bool: True if already processed
        """
        with get_db_lock():
            row = self.conn.execute(
                "SELECT 1 FROM processed_ids WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            return row is not None
    
    def mark_processed(self, entry_id):
        """
        Mark an entry as processed with current timestamp
        
        Args:
            entry_id: Unique identifier to mark as processed
        """
        with get_db_lock():
            self.conn.execute(
                "INSERT OR REPLACE INTO processed_ids (entry_id, timestamp) VALUES (?, ?)",
                (entry_id, time.time())
            )
            self.conn.commit()
        logger.debug(f"Marked as processed: {entry_id}")
    
    def add_embedding(self, content, embedding, entry_id=None):
        """
        Store an embedding for duplicate detection
        
        Args:
            content: Text content to hash for unique ID
            embedding: List/array of embedding values
            entry_id: Optional entry ID to link embedding back to original entry
        
        Returns:
            str: Hash key for the stored embedding
        """
        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
        embedding_list = embedding if isinstance(embedding, list) else embedding.tolist()
        now = time.time()
        preview = content[:100]

        with get_db_lock():
            self.conn.execute(
                """INSERT OR REPLACE INTO embeddings
                   (content_hash, embedding, timestamp, preview, content, entry_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (content_hash, json.dumps(embedding_list), now, preview, content, entry_id)
            )
            self.conn.commit()

            # Keep in-memory cache in sync (pre-compute numpy array and norm).
            # Copy-on-write: publish a new dict rather than mutating the live
            # one — see the contract note on _load_embeddings_cache.
            embedding_np = np.array(embedding_list)
            norm = np.linalg.norm(embedding_np)
            new_cache = dict(self._embeddings_cache)
            new_cache[content_hash] = {
                'embedding': embedding_list,
                'embedding_np': embedding_np,
                'norm': norm,
                'timestamp': now,
                'preview': preview,
                'content': content,
                'entry_id': entry_id
            }
            self._embeddings_cache = new_cache
        
        logger.debug(f"Stored embedding for: {content[:50]}...")
        return content_hash

    def find_best_match(self, embedding):
        """
        Single-pass scan over all cached embeddings to find the best match.
        Returns the highest cosine similarity and its metadata so the caller
        can check against both DUPLICATE_THRESHOLD and SIMILARITY_THRESHOLD
        without scanning twice.

        Args:
            embedding: Embedding vector to compare

        Returns:
            tuple: (best_similarity, best_preview, best_content, best_entry_id) or (0.0, None, None, None)
        """
        # Lock-free read: _embeddings_cache is copy-on-write (writers publish a
        # new dict, never mutate the live one — see _load_embeddings_cache), so
        # grabbing the current reference gives a stable dict to iterate. This
        # runs on the event loop, so it must not contend for the DB lock.
        snapshot = self._embeddings_cache.values()

        if not snapshot:
            return 0.0, None, None, None

        query = np.array(embedding)
        query_norm = np.linalg.norm(query)
        if query_norm == 0:
            return 0.0, None, None, None

        best_sim = 0.0
        best_data = None

        for data in snapshot:
            stored_norm = data['norm']
            if stored_norm == 0:
                continue
            if len(data['embedding_np']) != len(query):
                continue
            similarity = np.dot(query, data['embedding_np']) / (query_norm * stored_norm)
            if similarity > best_sim:
                best_sim = similarity
                best_data = data

        if best_data is None:
            return 0.0, None, None, None

        return best_sim, best_data['preview'], best_data.get('content', best_data['preview']), best_data.get('entry_id')

    def find_top_matches(self, embedding, threshold=0.0, limit=3):
        """
        Return the top-N matches above threshold, sorted by similarity descending.
        Used for the similarity check so we verify against the most likely candidates,
        not just the single highest-scoring entry (which may not be the actual duplicate).

        Returns:
            list of tuples: [(similarity, preview, content, entry_id), ...]
        """
        # Lock-free read of the copy-on-write cache — see find_best_match.
        snapshot = self._embeddings_cache.values()

        if not snapshot:
            return []

        query = np.array(embedding)
        query_norm = np.linalg.norm(query)
        if query_norm == 0:
            return []

        results = []
        for data in snapshot:
            stored_norm = data['norm']
            if stored_norm == 0:
                continue
            if len(data['embedding_np']) != len(query):
                continue
            similarity = np.dot(query, data['embedding_np']) / (query_norm * stored_norm)
            if similarity >= threshold:
                results.append((
                    similarity,
                    data['preview'],
                    data.get('content', data['preview']),
                    data.get('entry_id')
                ))

        results.sort(key=lambda x: x[0], reverse=True)
        return results[:limit]

    def find_similar(self, embedding, threshold=None):
        """
        Find similar embeddings above threshold using cosine similarity.
        Kept for backward compatibility.

        Args:
            embedding: Embedding vector to compare
            threshold: Similarity threshold (0.0-1.0)

        Returns:
            tuple: (is_match, similarity_score, matching_preview, full_content) or (False, 0.0, None, None)
        """
        import config
        if threshold is None:
            threshold = config.DUPLICATE_THRESHOLD

        best_sim, preview, content, entry_id = self.find_best_match(embedding)
        if best_sim >= threshold:
            logger.info(f"Duplicate detected! Similarity: {best_sim:.3f} - {preview}")
            return True, best_sim, preview, content

        return False, 0.0, None, None
    
    def cleanup_old_entries(self):
        """Prune old rows: embeddings at DB_RETENTION_HOURS, processed_ids at the
        (much longer) PROCESSED_IDS_RETENTION_HOURS. The windows are split so an
        item that lingers in a slow RSS feed past the embedding window doesn't
        also lose its "already posted" flag and get re-posted."""
        import config
        current_time = time.time()
        embedding_cutoff = current_time - (config.DB_RETENTION_HOURS * 3600)
        processed_retention = getattr(
            config, 'PROCESSED_IDS_RETENTION_HOURS', config.DB_RETENTION_HOURS
        )
        processed_cutoff = current_time - (processed_retention * 3600)

        with get_db_lock():
            # Clean processed IDs
            cursor = self.conn.execute(
                "DELETE FROM processed_ids WHERE timestamp < ?", (processed_cutoff,)
            )
            deleted_ids = cursor.rowcount

            # Clean embeddings: capture content_hashes before DELETE so we can
            # drop them from the in-memory cache without a full reload.
            stale_hashes = [
                row['content_hash'] for row in
                self.conn.execute(
                    "SELECT content_hash FROM embeddings WHERE timestamp < ?", (embedding_cutoff,)
                ).fetchall()
            ]
            cursor = self.conn.execute(
                "DELETE FROM embeddings WHERE timestamp < ?", (embedding_cutoff,)
            )
            deleted_embeddings = cursor.rowcount

            # Copy-on-write removal — see contract note on _load_embeddings_cache
            if stale_hashes:
                stale_set = set(stale_hashes)
                self._embeddings_cache = {
                    k: v for k, v in self._embeddings_cache.items() if k not in stale_set
                }

            self.conn.commit()

        if deleted_ids:
            logger.info(f"Cleaned up {deleted_ids} old processed IDs")
        if deleted_embeddings:
            logger.info(f"Cleaned up {deleted_embeddings} old embeddings")
    
    def get_stats(self):
        """
        Get database statistics
        
        Returns:
            dict: Statistics about the database
        """
        with get_db_lock():
            processed_count = self.conn.execute("SELECT COUNT(*) FROM processed_ids").fetchone()[0]
            embeddings_count = len(self._embeddings_cache)
            mapping_count = self.conn.execute("SELECT COUNT(*) FROM message_mapping").fetchone()[0]

        return {
            'processed_ids': processed_count,
            'embeddings': embeddings_count,
            'message_mappings': mapping_count
        }
    
    def store_message_mapping(self, telegram_entry_id, telegram_message_id, discord_channel_id, discord_message_id, content=None, source_url=None, video_urls=None, category=None, source_type=None, reasoning=None, original_category=None, placement_reason=None, secondary_category=None, original_secondary_category=None, newsworthiness_score=None):
        """
        Store mapping between Telegram and Discord messages

        Args:
            telegram_entry_id: Full entry ID (e.g., 'telegram_channelname_123' or 'twitter_123')
            telegram_message_id: Numeric Telegram message ID (or 0 for non-Telegram)
            discord_channel_id: Discord channel ID where message was posted
            discord_message_id: Discord message ID
            content: The message content (for edit comparison)
            source_url: Original source URL (Twitter/X.com link, etc.)
            video_urls: List of video URLs (for Twitter entries with videos)
            category: Category the message was posted to
            source_type: Source type ('twitter', 'telegram', etc.) for re-categorization
            reasoning: Brief explanation of why this category was chosen
            original_category: The AI's initial category before any user re-categorization
            placement_reason: Human-readable explanation of why this entry is in its current category
            secondary_category: Optional second category for cross-topic entries
            original_secondary_category: AI's initial secondary category before user changes
            newsworthiness_score: Reaction-worthiness gate score (1-10) if the gate ran
        """
        with get_db_lock():
            self.conn.execute(
                """INSERT OR REPLACE INTO message_mapping
                   (entry_id, telegram_message_id, discord_channel_id, discord_message_id,
                    content, source_url, video_urls, category, source_type, reasoning, timestamp,
                    original_category, placement_reason, secondary_category, original_secondary_category,
                    newsworthiness_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    telegram_entry_id,
                    telegram_message_id,
                    discord_channel_id,
                    discord_message_id,
                    content,
                    source_url,
                    json.dumps(video_urls) if video_urls else '[]',
                    category,
                    source_type,
                    reasoning,
                    time.time(),
                    original_category if original_category is not None else category,
                    placement_reason,
                    secondary_category,
                    original_secondary_category if original_secondary_category is not None else secondary_category,
                    newsworthiness_score
                )
            )
            self.conn.commit()

        logger.debug(f"Stored message mapping: {telegram_entry_id} -> Discord {discord_message_id} (category: {category}, source_type: {source_type})")
    
    def get_discord_message_info(self, telegram_entry_id):
        """
        Get Discord message info for a Telegram entry
        
        Args:
            telegram_entry_id: Full entry ID (e.g., 'telegram_channelname_123')
        
        Returns:
            dict: Discord message info or None if not found
        """
        with get_db_lock():
            row = self.conn.execute(
                "SELECT * FROM message_mapping WHERE entry_id = ?", (telegram_entry_id,)
            ).fetchone()

            if row is None:
                return None

            keys = row.keys()
            return {
                'telegram_message_id': row['telegram_message_id'],
                'discord_channel_id': row['discord_channel_id'],
                'discord_message_id': row['discord_message_id'],
                'content': row['content'],
                'source_url': row['source_url'],
                'video_urls': json.loads(row['video_urls']) if row['video_urls'] else [],
                'category': row['category'],
                'source_type': row['source_type'],
                'reasoning': row['reasoning'],
                'timestamp': row['timestamp'],
                'user_edited': row['user_edited'] if 'user_edited' in keys else 0,
                'original_category': row['original_category'] if 'original_category' in keys else row['category'],
                'placement_reason': row['placement_reason'] if 'placement_reason' in keys else None,
                'secondary_category': row['secondary_category'] if 'secondary_category' in keys else None,
                'original_secondary_category': row['original_secondary_category'] if 'original_secondary_category' in keys else None,
                'newsworthiness_score': row['newsworthiness_score'] if 'newsworthiness_score' in keys else None
            }
    
    def get_gate_demotion_examples(self, limit=8, max_preview_length=120):
        """
        Entries the reaction gate passed (posted to a real category) but the user
        then demoted to ignore. Used as negative few-shot examples in the
        rate_newsworthiness prompt so the gate learns the user's taste.

        Returns:
            list of (content_preview, gate_score) tuples, newest first
        """
        with get_db_lock():
            rows = self.conn.execute(
                """SELECT content, newsworthiness_score FROM message_mapping
                   WHERE category = 'ignore'
                     AND newsworthiness_score IS NOT NULL
                     AND (placement_reason LIKE 'User re-categorization%'
                          OR placement_reason LIKE 'User label update%')
                     AND content IS NOT NULL AND TRIM(content) != ''
                   ORDER BY timestamp DESC LIMIT ?""",
                (limit,)
            ).fetchall()
            return [(row['content'][:max_preview_length], row['newsworthiness_score'])
                    for row in rows]

    def get_entry_id_by_discord_message(self, discord_message_id):
        """
        Fast reverse lookup: get entry ID from Discord message ID
        
        Uses SQL index on discord_message_id for fast lookups.
        
        Args:
            discord_message_id: Discord message ID (int)
        
        Returns:
            str: Entry ID or None if not found
        """
        with get_db_lock():
            row = self.conn.execute(
                "SELECT entry_id FROM message_mapping WHERE discord_message_id = ?",
                (discord_message_id,)
            ).fetchone()

            return row['entry_id'] if row else None
    
    def delete_message_mapping(self, entry_id):
        """
        Delete a message mapping by entry ID

        Args:
            entry_id: Entry ID to delete
        """
        with get_db_lock():
            self.conn.execute("DELETE FROM message_mapping WHERE entry_id = ?", (entry_id,))
            self.conn.commit()
        logger.debug(f"Deleted message mapping: {entry_id}")

    def mark_as_superseded(self, entry_id, superseded_by_entry_id):
        """
        Mark an entry as superseded rather than deleting it.

        Args:
            entry_id: The entry that was superseded
            superseded_by_entry_id: The new entry that superseded it
        """
        with get_db_lock():
            self.conn.execute(
                "UPDATE message_mapping SET superseded_by = ? WHERE entry_id = ?",
                (superseded_by_entry_id, entry_id)
            )
            self.conn.commit()
        logger.debug(f"Marked {entry_id} as superseded by {superseded_by_entry_id}")

    def update_superseded_channel_message_id(self, entry_id, channel_msg_id):
        """Store the Discord message ID of the archived copy in the superseded channel."""
        with get_db_lock():
            self.conn.execute(
                "UPDATE message_mapping SET superseded_channel_discord_message_id = ? WHERE entry_id = ?",
                (channel_msg_id, entry_id)
            )
            self.conn.commit()
        logger.debug(f"Stored superseded-channel message ID {channel_msg_id} for {entry_id}")

    # ------------------------------------------------------------------
    # Reaction tracking
    # ------------------------------------------------------------------

    def upsert_reaction(self, entry_id: str, emoji: str, user_ids: list) -> None:
        with get_db_lock():
            self.conn.execute(
                """INSERT INTO entry_reactions (entry_id, emoji, user_ids) VALUES (?, ?, ?)
                   ON CONFLICT(entry_id, emoji) DO UPDATE SET user_ids = excluded.user_ids""",
                (entry_id, emoji, json.dumps(user_ids))
            )
            self.conn.commit()

    def remove_reaction_user(self, entry_id: str, emoji: str, user_id: str) -> None:
        with get_db_lock():
            row = self.conn.execute(
                "SELECT user_ids FROM entry_reactions WHERE entry_id = ? AND emoji = ?",
                (entry_id, emoji)
            ).fetchone()
            if row is None:
                return
            users = json.loads(row['user_ids'])
            users = [u for u in users if u != user_id]
            if users:
                self.conn.execute(
                    "UPDATE entry_reactions SET user_ids = ? WHERE entry_id = ? AND emoji = ?",
                    (json.dumps(users), entry_id, emoji)
                )
            else:
                self.conn.execute(
                    "DELETE FROM entry_reactions WHERE entry_id = ? AND emoji = ?",
                    (entry_id, emoji)
                )
            self.conn.commit()

    def get_reactions(self, entry_id: str) -> dict:
        with get_db_lock():
            rows = self.conn.execute(
                "SELECT emoji, user_ids FROM entry_reactions WHERE entry_id = ?",
                (entry_id,)
            ).fetchall()
            return {row['emoji']: json.loads(row['user_ids']) for row in rows}

    # ------------------------------------------------------------------

    def get_ignore_entry_previews(self, limit=30, max_preview_length=150):
        """
        Get content previews for entries categorized as 'ignore', for use as negative
        examples in the AI system prompt. User-recategorized ignores (where the AI
        originally chose a different category) are prioritized as stronger signals.

        Args:
            limit: Maximum number of entries to return
            max_preview_length: Truncate each preview to this many characters

        Returns:
            list of (preview_str, user_flagged_bool) tuples
        """
        with get_db_lock():
            rows = self.conn.execute(
                """SELECT content, original_category
                   FROM message_mapping
                   WHERE category = 'ignore'
                     AND content IS NOT NULL
                     AND content != ''
                   ORDER BY
                       CASE WHEN original_category IS NOT NULL AND original_category != 'ignore' THEN 0 ELSE 1 END,
                       timestamp DESC
                   LIMIT ?""",
                (limit,)
            ).fetchall()
            results = []
            for row in rows:
                preview = (row['content'] or '')[:max_preview_length]
                user_flagged = (
                    row['original_category'] is not None and
                    row['original_category'] != 'ignore'
                )
                results.append((preview, user_flagged))
            return results
    
    def get_recategorization_examples(self, limit=20, max_preview_length=150):
        """
        Return correction pairs where the AI picked the wrong non-ignore category.
        Used to build the 'corrections' block in the enhanced system prompt.

        Returns:
            list of (content_preview, original_category, corrected_category) tuples
        """
        with get_db_lock():
            rows = self.conn.execute(
                """SELECT content, original_category, category, user_reason
                   FROM message_mapping
                   WHERE original_category IS NOT NULL
                     AND original_category != category
                     AND original_category != 'ignore'
                     AND category != 'ignore'
                     AND content IS NOT NULL
                     AND content != ''
                     AND timestamp > ?
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (time.time() - 7 * 86400, limit)
            ).fetchall()
            return [
                ((row['content'] or '')[:max_preview_length], row['original_category'], row['category'], row['user_reason'])
                for row in rows
            ]

    def get_ignore_promotion_examples(self, limit=15, max_preview_length=150):
        """
        Return entries where the AI posted to a real channel but the user moved them to ignore.
        Used to teach the AI what it should have ignored in the first place.

        Returns:
            list of (content_preview, original_category) tuples
        """
        with get_db_lock():
            rows = self.conn.execute(
                """SELECT content, original_category, user_reason
                   FROM message_mapping
                   WHERE original_category IS NOT NULL
                     AND original_category != 'ignore'
                     AND category = 'ignore'
                     AND content IS NOT NULL
                     AND content != ''
                     AND timestamp > ?
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (time.time() - 7 * 86400, limit)
            ).fetchall()
            return [
                ((row['content'] or '')[:max_preview_length], row['original_category'], row['user_reason'])
                for row in rows
            ]

    def get_ignore_rescue_examples(self, limit=10, max_preview_length=150):
        """
        Return entries where the AI assigned 'ignore' but a user promoted them to a real
        category. Used to teach the AI what it nearly missed.

        Returns:
            list of (content_preview, correct_category) tuples
        """
        with get_db_lock():
            rows = self.conn.execute(
                """SELECT content, category
                   FROM message_mapping
                   WHERE original_category = 'ignore'
                     AND category != 'ignore'
                     AND content IS NOT NULL
                     AND content != ''
                     AND timestamp > ?
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (time.time() - 7 * 86400, limit)
            ).fetchall()
            return [
                ((row['content'] or '')[:max_preview_length], row['category'])
                for row in rows
            ]

    def update_message_mapping_fields(self, entry_id, **kwargs):
        """
        Update specific fields on a message mapping
        
        Args:
            entry_id: Entry ID to update
            **kwargs: Fields to update (e.g., discord_message_id=123, category='crypto')
        """
        if not kwargs:
            return
        
        # Handle video_urls specially (needs JSON encoding)
        if 'video_urls' in kwargs:
            kwargs['video_urls'] = json.dumps(kwargs['video_urls']) if kwargs['video_urls'] else '[]'
        
        set_clause = ", ".join(f"{key} = ?" for key in kwargs)
        values = list(kwargs.values()) + [entry_id]

        with get_db_lock():
            self.conn.execute(
                f"UPDATE message_mapping SET {set_clause} WHERE entry_id = ?",
                values
            )
            self.conn.commit()
        logger.debug(f"Updated message mapping for {entry_id}: {list(kwargs.keys())}")
    
    def delete_processed(self, entry_id):
        """
        Remove an entry from the processed IDs table
        
        Args:
            entry_id: Entry ID to remove
        """
        with get_db_lock():
            self.conn.execute("DELETE FROM processed_ids WHERE entry_id = ?", (entry_id,))
            self.conn.commit()
        logger.debug(f"Removed from processed IDs: {entry_id}")
    
    def delete_embedding_by_entry_id(self, entry_id):
        """
        Remove all embeddings associated with a given entry_id.

        Used by the reprocess pipeline to clear stale embeddings before re-running,
        so the entry won't self-detect as a duplicate during reprocessing.
        Must look up by entry_id (not content hash) because OCR entries store embeddings
        generated from combined_content (content + OCR text), not plain content.

        Args:
            entry_id: The entry ID whose embeddings should be removed
        """
        with get_db_lock():
            rows = self.conn.execute(
                "SELECT content_hash FROM embeddings WHERE entry_id = ?", (entry_id,)
            ).fetchall()
            for row in rows:
                self.conn.execute("DELETE FROM embeddings WHERE content_hash = ?", (row['content_hash'],))
            # Copy-on-write removal — see contract note on _load_embeddings_cache
            if rows:
                dead = {row['content_hash'] for row in rows}
                self._embeddings_cache = {
                    k: v for k, v in self._embeddings_cache.items() if k not in dead
                }
            self.conn.commit()
        logger.debug(f"Deleted {len(rows)} embedding(s) for entry_id: {entry_id}")

    def delete_embedding_by_content(self, content):
        """
        Remove an embedding by its content (used when removing entries)
        
        Args:
            content: The content text whose embedding should be removed
        """
        import hashlib
        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()

        with get_db_lock():
            self.conn.execute("DELETE FROM embeddings WHERE content_hash = ?", (content_hash,))
            self.conn.commit()

            # Copy-on-write removal — see contract note on _load_embeddings_cache
            if content_hash in self._embeddings_cache:
                new_cache = dict(self._embeddings_cache)
                new_cache.pop(content_hash, None)
                self._embeddings_cache = new_cache
        logger.debug(f"Deleted embedding for content hash: {content_hash}")
    
    def get_all_message_mappings(self):
        """
        Get all message mappings as a dict (for iteration/search)
        
        Returns:
            dict: All mappings keyed by entry_id
        """
        with get_db_lock():
            rows = self.conn.execute("SELECT * FROM message_mapping").fetchall()
            result = {}
            for row in rows:
                result[row['entry_id']] = {
                    'telegram_message_id': row['telegram_message_id'],
                    'discord_channel_id': row['discord_channel_id'],
                    'discord_message_id': row['discord_message_id'],
                    'content': row['content'],
                    'source_url': row['source_url'],
                    'video_urls': json.loads(row['video_urls']) if row['video_urls'] else [],
                    'category': row['category'],
                    'source_type': row['source_type'],
                    'reasoning': row['reasoning'],
                    'timestamp': row['timestamp']
                }
            return result
    
    def search_message_mappings(self, query):
        """
        Search message mappings by content or entry_id
        
        Args:
            query: Search string
        
        Returns:
            dict: Matching mappings keyed by entry_id
        """
        with get_db_lock():
            rows = self.conn.execute(
                "SELECT * FROM message_mapping WHERE entry_id LIKE ? OR content LIKE ?",
                (f"%{query}%", f"%{query}%")
            ).fetchall()
            result = {}
            for row in rows:
                result[row['entry_id']] = {
                    'telegram_message_id': row['telegram_message_id'],
                    'discord_channel_id': row['discord_channel_id'],
                    'discord_message_id': row['discord_message_id'],
                    'content': row['content'],
                    'source_url': row['source_url'],
                    'video_urls': json.loads(row['video_urls']) if row['video_urls'] else [],
                    'category': row['category'],
                    'source_type': row['source_type'],
                    'reasoning': row['reasoning'],
                    'timestamp': row['timestamp']
                }
            return result
