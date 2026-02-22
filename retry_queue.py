"""
Retry queue for failed Twitter media extractions
"""
import json
from utils import logger
from db_connection import get_db_connection


class RetryQueue:
    """Manages retry attempts for entries where gallery-dl failed"""
    
    def __init__(self, max_retries=3, retry_delay_cycles=2):
        """
        Initialize retry queue
        
        Args:
            max_retries: Maximum number of retry attempts per entry
            retry_delay_cycles: Number of poll cycles to wait before retrying
        """
        self.max_retries = max_retries
        self.retry_delay_cycles = retry_delay_cycles
        self.conn = get_db_connection()
        self.current_cycle = 0
    
    def add_entry(self, entry):
        """
        Add an entry to the retry queue
        
        Args:
            entry: Entry dictionary that failed to process
        """
        entry_id = entry['id']
        
        row = self.conn.execute(
            "SELECT retry_count FROM retry_queue WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        
        if row:
            # Increment retry count
            new_count = row['retry_count'] + 1
            self.conn.execute(
                "UPDATE retry_queue SET retry_count = ?, last_attempt_cycle = ? WHERE entry_id = ?",
                (new_count, self.current_cycle, entry_id)
            )
            self.conn.commit()
            logger.info(
                f"Entry added to retry queue (attempt {new_count}/{self.max_retries}): {entry_id}"
            )
        else:
            # First retry
            self.conn.execute(
                """INSERT INTO retry_queue 
                   (entry_id, entry_data, retry_count, first_attempt_cycle, last_attempt_cycle, reason)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (entry_id, json.dumps(entry), 1, self.current_cycle, self.current_cycle,
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
        
        rows = self.conn.execute(
            "SELECT entry_id, entry_data, retry_count, last_attempt_cycle FROM retry_queue"
        ).fetchall()
        
        for row in rows:
            entry_id = row['entry_id']
            retry_count = row['retry_count']
            last_attempt_cycle = row['last_attempt_cycle'] or 0
            
            # Check if we've exceeded max retries
            if retry_count > self.max_retries:
                logger.warning(
                    f"Entry exceeded max retries ({self.max_retries}), removing from queue: {entry_id}"
                )
                self.remove_entry(entry_id, reason="max_retries_exceeded")
                continue
            
            # Check if enough cycles have passed since last attempt
            cycles_since_last = self.current_cycle - last_attempt_cycle
            if cycles_since_last >= self.retry_delay_cycles:
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
    
    def increment_cycle(self):
        """Increment the cycle counter (call at start of each poll cycle)"""
        self.current_cycle += 1
    
    def get_stats(self):
        """
        Get statistics about the retry queue
        
        Returns:
            dict: Statistics including queue size and retry counts
        """
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
        # Since we're tracking by cycles, we can estimate based on cycle count
        # Assuming 5 minute poll interval: 12 cycles per hour
        max_cycles = max_age_hours * 12
        
        cutoff_cycle = self.current_cycle - max_cycles
        
        cursor = self.conn.execute(
            "DELETE FROM retry_queue WHERE first_attempt_cycle < ?", (cutoff_cycle,)
        )
        self.conn.commit()
        
        removed = cursor.rowcount
        if removed > 0:
            logger.info(f"Cleaned up {removed} expired entries from retry queue")
