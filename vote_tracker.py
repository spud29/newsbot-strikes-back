"""
Vote Tracking System for Discord "Not Valuable" Button
Manages user votes on Discord messages to determine if content should be removed
"""
import json
import time
from utils import logger
from db_connection import get_db_connection


class VoteTracker:
    """Tracks votes on Discord messages for content removal"""
    
    def __init__(self):
        """Initialize vote tracker with shared SQLite connection"""
        self.conn = get_db_connection()
        
        count = self.conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
        logger.info(f"VoteTracker initialized with {count} active votes")
    
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
        
        # Get existing vote data
        row = self.conn.execute(
            "SELECT voters, timestamp, entry_id, content, category, discord_channel_id FROM votes WHERE discord_message_id = ?",
            (message_key,)
        ).fetchone()
        
        if row:
            voters = json.loads(row['voters'])
            
            # Check if user already voted
            if voter_id in voters:
                logger.info(f"User {voter_id} already voted on message {message_key}")
                return len(voters), True
            
            # Add vote
            voters.append(voter_id)
            self.conn.execute(
                "UPDATE votes SET voters = ? WHERE discord_message_id = ?",
                (json.dumps(voters), message_key)
            )
        else:
            # New vote tracking entry
            voters = [voter_id]
            entry_id = entry_data.get('entry_id') if entry_data else None
            content = entry_data.get('content') if entry_data else None
            category = entry_data.get('category') if entry_data else None
            discord_channel_id = entry_data.get('discord_channel_id') if entry_data else None
            
            self.conn.execute(
                """INSERT INTO votes (discord_message_id, voters, timestamp, entry_id, content, category, discord_channel_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (message_key, json.dumps(voters), time.time(), entry_id, content, category, discord_channel_id)
            )
        
        self.conn.commit()
        
        vote_count = len(voters)
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
        row = self.conn.execute(
            "SELECT * FROM votes WHERE discord_message_id = ?", (message_key,)
        ).fetchone()
        
        if row is None:
            return None
        
        return {
            'voters': json.loads(row['voters']),
            'timestamp': row['timestamp'],
            'entry_id': row['entry_id'],
            'content': row['content'],
            'category': row['category'],
            'discord_channel_id': row['discord_channel_id']
        }
    
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
        
        cursor = self.conn.execute(
            "DELETE FROM votes WHERE discord_message_id = ?", (message_key,)
        )
        self.conn.commit()
        
        if cursor.rowcount > 0:
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
        cutoff_time = time.time() - (max_age_hours * 3600)
        
        cursor = self.conn.execute(
            "DELETE FROM votes WHERE timestamp < ?", (cutoff_time,)
        )
        self.conn.commit()
        
        deleted = cursor.rowcount
        if deleted:
            logger.info(f"Cleaned up {deleted} old vote tracking entries")
        
        return deleted
    
    def get_stats(self):
        """
        Get statistics about current votes
        
        Returns:
            dict: Statistics
        """
        total_messages = self.conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
        
        # Sum up total votes across all entries
        rows = self.conn.execute("SELECT voters FROM votes").fetchall()
        total_votes = sum(len(json.loads(row['voters'])) for row in rows)
        
        return {
            'total_messages_with_votes': total_messages,
            'total_votes': total_votes
        }
