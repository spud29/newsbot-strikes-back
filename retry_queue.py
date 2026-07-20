"""
Retry queue for failed Twitter media extractions
"""
import json
import time
import config
from utils import logger
from db_connection import get_db_connection, get_db_lock


class RetryQueue:
    """Manages retry attempts for entries where gallery-dl failed.

    Timing is wall-clock based (persisted first/last attempt timestamps).
    The old in-memory cycle counter reset to 0 on every restart while the
    queue rows persisted, which froze all pending retries until the new
    counter caught up with the pre-restart cycle numbers.
    """

    def __init__(self, max_retries=3, retry_delay_cycles=2):
        """
        Initialize retry queue

        Args:
            max_retries: Maximum number of retry attempts per entry
            retry_delay_cycles: Poll cycles to wait before retrying; converted
                to seconds via config.POLL_INTERVAL so restarts don't reset it
        """
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_cycles * getattr(config, 'POLL_INTERVAL', 300)
        self.conn = get_db_connection()
    
    def add_entry(self, entry):
        """
        Add an entry to the retry queue
        
        Args:
            entry: Entry dictionary that failed to process
        """
        entry_id = entry['id']
        now = time.time()

        with get_db_lock():
            row = self.conn.execute(
                "SELECT retry_count FROM retry_queue WHERE entry_id = ?", (entry_id,)
            ).fetchone()

            if row:
                # Increment retry count
                new_count = row['retry_count'] + 1
                self.conn.execute(
                    "UPDATE retry_queue SET retry_count = ?, last_attempt_ts = ? WHERE entry_id = ?",
                    (new_count, now, entry_id)
                )
                self.conn.commit()
                logger.info(
                    f"Entry added to retry queue (attempt {new_count}/{self.max_retries}): {entry_id}"
                )
            else:
                # First retry
                self.conn.execute(
                    """INSERT INTO retry_queue
                       (entry_id, entry_data, retry_count, first_attempt_ts, last_attempt_ts, reason)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (entry_id, json.dumps(entry), 1, now, now,
                     'gallery-dl failed to extract content')
                )
                self.conn.commit()
                logger.info(f"Entry added to retry queue (first attempt): {entry_id}")
    
    def get_entries_to_retry(self):
        """
        Get entries that should be retried in this cycle
        
        Returns:
            list: List of entry dictionaries to retry
        """
        entries_to_retry = []
        now = time.time()

        with get_db_lock():
            rows = self.conn.execute(
                "SELECT entry_id, entry_data, retry_count, last_attempt_ts FROM retry_queue"
            ).fetchall()

            for row in rows:
                entry_id = row['entry_id']
                retry_count = row['retry_count']
                # Legacy/NULL timestamps count as "long ago" → eligible now
                last_attempt_ts = row['last_attempt_ts'] or 0

                # Check if we've exceeded max retries
                if retry_count > self.max_retries:
                    logger.warning(
                        f"Entry exceeded max retries ({self.max_retries}), removing from queue: {entry_id}"
                    )
                    self._remove_entry_locked(entry_id, reason="max_retries_exceeded")
                    continue

                # Check if enough wall-clock time has passed since the last attempt
                if (now - last_attempt_ts) >= self.retry_delay_seconds:
                    entry_data = json.loads(row['entry_data'])
                    entries_to_retry.append(entry_data)

        return entries_to_retry
    
    def remove_entry(self, entry_id, reason="success"):
        """
        Remove an entry from the retry queue

        Args:
            entry_id: ID of the entry to remove
            reason: Reason for removal (success, max_retries_exceeded, etc.)
        """
        with get_db_lock():
            self._remove_entry_locked(entry_id, reason=reason)

    def _remove_entry_locked(self, entry_id, reason="success"):
        """Internal remove_entry — caller must already hold get_db_lock()."""
        row = self.conn.execute(
            "SELECT retry_count FROM retry_queue WHERE entry_id = ?", (entry_id,)
        ).fetchone()

        if row:
            retry_count = row['retry_count']
            self.conn.execute("DELETE FROM retry_queue WHERE entry_id = ?", (entry_id,))
            self.conn.commit()

            if reason == "success":
                logger.info(f"✓ Entry successfully processed after {retry_count} retry(ies): {entry_id}")
            else:
                logger.warning(f"Entry removed from retry queue ({reason}): {entry_id}")
    
    def get_stats(self):
        """
        Get statistics about the retry queue
        
        Returns:
            dict: Statistics including queue size and retry counts
        """
        with get_db_lock():
            rows = self.conn.execute("SELECT retry_count FROM retry_queue").fetchall()

            if not rows:
                return {
                    'total_entries': 0,
                    'by_retry_count': {}
                }

            by_retry_count = {}
            for row in rows:
                count = row['retry_count']
                by_retry_count[count] = by_retry_count.get(count, 0) + 1

            return {
                'total_entries': len(rows),
                'by_retry_count': by_retry_count
            }
    
    def cleanup_old_entries(self, max_age_hours=24):
        """
        Remove entries older than max_age_hours
        
        Args:
            max_age_hours: Maximum age in hours
        """
        cutoff_ts = time.time() - max_age_hours * 3600

        with get_db_lock():
            # COALESCE: rows migrated from the old cycle-based schema were
            # backfilled at migration time, but treat any stray NULL as expired.
            cursor = self.conn.execute(
                "DELETE FROM retry_queue WHERE COALESCE(first_attempt_ts, 0) < ?", (cutoff_ts,)
            )
            self.conn.commit()
            removed = cursor.rowcount

        if removed > 0:
            logger.info(f"Cleaned up {removed} expired entries from retry queue")
