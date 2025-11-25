"""
Retry queue for failed Twitter media extractions
"""
import json
import os
import time
from utils import logger

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
        self.queue_file = os.path.join("data", "retry_queue.json")
        self.queue = self._load_queue()
        self.current_cycle = 0
    
    def _load_queue(self):
        """Load retry queue from file"""
        if os.path.exists(self.queue_file):
            try:
                with open(self.queue_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading retry queue: {e}")
                return {}
        return {}
    
    def _save_queue(self):
        """Save retry queue to file"""
        try:
            os.makedirs("data", exist_ok=True)
            with open(self.queue_file, 'w', encoding='utf-8') as f:
                json.dump(self.queue, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving retry queue: {e}")
    
    def add_entry(self, entry):
        """
        Add an entry to the retry queue
        
        Args:
            entry: Entry dictionary that failed to process
        """
        entry_id = entry['id']
        
        if entry_id in self.queue:
            # Increment retry count
            self.queue[entry_id]['retry_count'] += 1
            self.queue[entry_id]['last_attempt_cycle'] = self.current_cycle
            logger.info(
                f"Entry added to retry queue (attempt {self.queue[entry_id]['retry_count']}/{self.max_retries}): {entry_id}"
            )
        else:
            # First retry
            self.queue[entry_id] = {
                'entry': entry,
                'retry_count': 1,
                'first_attempt_cycle': self.current_cycle,
                'last_attempt_cycle': self.current_cycle,
                'reason': 'gallery-dl failed to extract content'
            }
            logger.info(f"Entry added to retry queue (first attempt): {entry_id}")
        
        self._save_queue()
    
    def get_entries_to_retry(self):
        """
        Get entries that should be retried in this cycle
        
        Returns:
            list: List of entry dictionaries to retry
        """
        entries_to_retry = []
        
        for entry_id, retry_info in list(self.queue.items()):
            retry_count = retry_info['retry_count']
            last_attempt_cycle = retry_info['last_attempt_cycle']
            
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
                entries_to_retry.append(retry_info['entry'])
        
        return entries_to_retry
    
    def remove_entry(self, entry_id, reason="success"):
        """
        Remove an entry from the retry queue
        
        Args:
            entry_id: ID of the entry to remove
            reason: Reason for removal (success, max_retries_exceeded, etc.)
        """
        if entry_id in self.queue:
            retry_count = self.queue[entry_id]['retry_count']
            del self.queue[entry_id]
            self._save_queue()
            
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
        if not self.queue:
            return {
                'total_entries': 0,
                'by_retry_count': {}
            }
        
        by_retry_count = {}
        for retry_info in self.queue.values():
            count = retry_info['retry_count']
            by_retry_count[count] = by_retry_count.get(count, 0) + 1
        
        return {
            'total_entries': len(self.queue),
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
        
        removed = 0
        for entry_id, retry_info in list(self.queue.items()):
            age_cycles = self.current_cycle - retry_info['first_attempt_cycle']
            if age_cycles > max_cycles:
                self.remove_entry(entry_id, reason="expired")
                removed += 1
        
        if removed > 0:
            logger.info(f"Cleaned up {removed} expired entries from retry queue")

