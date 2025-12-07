"""
Vote Tracking System for Discord "Not Valuable" Button
Manages user votes on Discord messages to determine if content should be removed
"""
import json
import os
import time
from utils import logger, ensure_directory


class VoteTracker:
    """Tracks votes on Discord messages for content removal"""
    
    def __init__(self, votes_path="data/vote_tracking.json"):
        """
        Initialize vote tracker
        
        Args:
            votes_path: Path to vote tracking JSON file
        """
        ensure_directory('data')
        self.votes_path = votes_path
        self.votes = self._load_votes()
        logger.info(f"VoteTracker initialized with {len(self.votes)} active votes")
    
    def _load_votes(self):
        """Load votes from JSON file"""
        try:
            if os.path.exists(self.votes_path):
                with open(self.votes_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Error loading votes from {self.votes_path}: {e}")
            return {}
    
    def _save_votes(self):
        """Save votes to JSON file"""
        try:
            with open(self.votes_path, 'w', encoding='utf-8') as f:
                json.dump(self.votes, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving votes to {self.votes_path}: {e}")
    
    def add_vote(self, discord_message_id, voter_user_id, entry_data=None):
        """
        Add a vote for a Discord message
        
        Args:
            discord_message_id: Discord message ID (as string)
            voter_user_id: Discord user ID who voted (as string)
            entry_data: Optional dict with entry metadata (entry_id, content, category, etc.)
        
        Returns:
            tuple: (vote_count, is_duplicate_vote)
                - vote_count: Current number of unique votes
                - is_duplicate_vote: True if this user already voted
        """
        message_key = str(discord_message_id)
        voter_id = str(voter_user_id)
        
        # Initialize vote tracking for this message if not exists
        if message_key not in self.votes:
            self.votes[message_key] = {
                'voters': [],
                'timestamp': time.time()
            }
            
            # Add entry metadata if provided
            if entry_data:
                self.votes[message_key].update(entry_data)
        
        # Check if user already voted
        if voter_id in self.votes[message_key]['voters']:
            logger.info(f"User {voter_id} already voted on message {message_key}")
            return len(self.votes[message_key]['voters']), True
        
        # Add the vote
        self.votes[message_key]['voters'].append(voter_id)
        self._save_votes()
        
        vote_count = len(self.votes[message_key]['voters'])
        logger.info(f"Vote added for message {message_key} by user {voter_id}. Total votes: {vote_count}")
        
        return vote_count, False
    
    def get_votes(self, discord_message_id):
        """
        Get vote data for a specific message
        
        Args:
            discord_message_id: Discord message ID
        
        Returns:
            dict: Vote data or None if no votes exist
        """
        message_key = str(discord_message_id)
        return self.votes.get(message_key)
    
    def get_vote_count(self, discord_message_id):
        """
        Get the number of votes for a message
        
        Args:
            discord_message_id: Discord message ID
        
        Returns:
            int: Number of unique votes
        """
        vote_data = self.get_votes(discord_message_id)
        if vote_data:
            return len(vote_data.get('voters', []))
        return 0
    
    def remove_tracking(self, discord_message_id):
        """
        Remove vote tracking for a message (cleanup after action taken)
        
        Args:
            discord_message_id: Discord message ID
        
        Returns:
            bool: True if tracking was removed, False if it didn't exist
        """
        message_key = str(discord_message_id)
        
        if message_key in self.votes:
            del self.votes[message_key]
            self._save_votes()
            logger.info(f"Vote tracking removed for message {message_key}")
            return True
        
        return False
    
    def cleanup_old_votes(self, max_age_hours=48):
        """
        Clean up old vote tracking data (for stale votes that never reached threshold)
        
        Args:
            max_age_hours: Maximum age in hours before cleanup
        
        Returns:
            int: Number of entries cleaned up
        """
        current_time = time.time()
        cutoff_time = current_time - (max_age_hours * 3600)
        
        old_entries = [
            msg_id for msg_id, data in self.votes.items()
            if data.get('timestamp', 0) < cutoff_time
        ]
        
        for msg_id in old_entries:
            del self.votes[msg_id]
        
        if old_entries:
            self._save_votes()
            logger.info(f"Cleaned up {len(old_entries)} old vote tracking entries")
        
        return len(old_entries)
    
    def get_stats(self):
        """
        Get statistics about current votes
        
        Returns:
            dict: Statistics
        """
        total_messages = len(self.votes)
        total_votes = sum(len(data.get('voters', [])) for data in self.votes.values())
        
        return {
            'total_messages_with_votes': total_messages,
            'total_votes': total_votes
        }










