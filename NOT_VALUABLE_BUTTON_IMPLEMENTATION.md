# "Not Valuable" Button Implementation Summary

## Overview

A complete feedback learning system has been implemented that allows Discord users to vote on content quality. When 2 unique users vote that an entry is "not valuable", the entry is removed and used to improve future categorization decisions through machine learning.

## Features Implemented

### 1. Vote Tracking System (`vote_tracker.py`)
- **VoteTracker** class manages user votes on Discord messages
- Tracks unique voters per message (prevents duplicate votes from same user)
- Persistent storage in `data/vote_tracking.json`
- Automatic cleanup of old vote data (stale votes after 48 hours)

### 2. Removed Entries Database (`removed_entries.py`)
- **RemovedEntriesDB** class stores all removed entries with metadata
- Persistent storage in `data/removed_entries.json`
- Each entry includes:
  - Entry ID, content, category
  - Voter IDs (who voted to remove)
  - Discord message/channel IDs
  - Source URL
  - Timestamp
- API for retrieving recent entries for system prompt enhancement
- Cleanup functionality (removes entries older than 90 days by default)

### 3. Discord Button UI (`discord_poster.py`)
Three new View classes added:

#### NotValuableView
- Displays "Not Valuable" button on Discord messages
- Tracks votes and updates button label (e.g., "Not Valuable (1/2)")
- Prevents duplicate votes from same user
- Triggers removal when threshold reached (default: 2 votes)

#### CombinedButtonView
- Combines Perplexity search button with Not Valuable button
- Both buttons coexist on same message
- Maintains separate functionality for each button

**Removal Process (automatic when threshold reached):**
1. Deletes Discord message
2. Removes from database (processed_ids, embeddings, message_mapping)
3. Stores in removed_entries.json with voter information
4. Cleans up vote tracking
5. Sends confirmation to voters

### 4. Feedback Learning System (`ollama_client.py`)
Enhanced the OllamaClient with:

#### generate_enhanced_system_prompt()
- Loads base system prompt from config
- Fetches recent removed entries (configurable count)
- Appends negative examples section
- Caches enhanced prompt for 1 hour (reduces overhead)

#### Updated categorize()
- Now uses enhanced system prompt automatically
- Includes up to 20 recent removed entries as "avoid" examples
- Teaches the AI to recognize and ignore low-value content

**Example Enhanced Prompt Structure:**
```
[Base System Prompt]

============================================================
IMPORTANT: Based on user feedback, the following types of content should be categorized as 'ignore':

1. [Example removed content preview 1]
2. [Example removed content preview 2]
...
20. [Example removed content preview 20]

============================================================
Avoid posting content similar to the examples above. When in doubt, use 'ignore'.
```

### 5. Configuration (`config.py`)
New configuration options added:

```python
# Feedback Learning Configuration
FEEDBACK_LEARNING_ENABLED = True  # Enable learning from user feedback
FEEDBACK_EXAMPLES_COUNT = 20  # Number of removed entries to include in system prompt
NOT_VALUABLE_BUTTON_ENABLED = True  # Enable "Not Valuable" button
NOT_VALUABLE_BUTTON_LABEL = "Not Valuable"
NOT_VALUABLE_BUTTON_EMOJI = "🗑️"
NOT_VALUABLE_BUTTON_STYLE = "danger"  # primary, secondary, success, danger
NOT_VALUABLE_VOTES_REQUIRED = 2  # Number of unique votes needed
```

### 6. Dashboard Integration (`dashboard.py`)
New dashboard page and API endpoints:

#### New Page: `/removed`
- View all removed entries
- Statistics dashboard:
  - Total removed entries
  - Removed in last 7 days
  - Feedback learning status
  - Category breakdown
- Entry details modal
- Restore functionality (for mistakes)

#### API Endpoints:
- `GET /api/removed-entries` - List all removed entries with stats
- `POST /api/removed-entries/{entry_id}/restore` - Restore wrongly removed entry

### 7. Main Bot Integration (`main.py`)
Updated bot initialization:
- Passes `database`, `vote_tracker`, and `removed_entries_db` to DiscordPoster
- Passes `removed_entries_db` to OllamaClient for feedback learning
- Includes `entry_id` in post_message calls (required for vote tracking)

## File Structure

### New Files Created:
```
vote_tracker.py              # Vote tracking system
removed_entries.py           # Removed entries database
templates/removed.html       # Dashboard page for viewing removed entries
NOT_VALUABLE_BUTTON_IMPLEMENTATION.md  # This file
```

### Modified Files:
```
config.py                    # Added feedback learning configuration
ollama_client.py             # Enhanced system prompt with feedback learning
discord_poster.py            # Added NotValuableView and CombinedButtonView
main.py                      # Integrated new components
dashboard.py                 # Added removed entries API and page
templates/base.html          # Added navigation link to removed entries page
```

### Data Files (auto-created):
```
data/vote_tracking.json      # Active votes
data/removed_entries.json    # Historical removed entries
```

## Usage

### For Discord Users

1. **Voting on Content:**
   - Each posted message has a "Not Valuable 🗑️" button
   - Click the button to vote (ephemeral response confirms vote)
   - Button updates to show vote count: "Not Valuable (1/2)"
   - Cannot vote twice on same message

2. **Automatic Removal:**
   - When 2 unique users vote, message is automatically deleted
   - All voters receive confirmation
   - Entry is used to improve future content filtering

### For Administrators

1. **Monitor Removed Entries:**
   - Visit Dashboard → Removed tab
   - View statistics and full list of removed entries
   - Click "View" to see full entry details

2. **Restore Mistakes:**
   - Click entry to view details
   - Click "Restore Entry" button
   - Entry is removed from feedback database

3. **Configure Behavior:**
   - Edit `config.py` to adjust:
     - Vote threshold (default: 2)
     - Feedback learning enabled/disabled
     - Number of examples in system prompt
     - Button appearance

4. **Disable Feature:**
   ```python
   # In config.py
   NOT_VALUABLE_BUTTON_ENABLED = False  # Disable button
   FEEDBACK_LEARNING_ENABLED = False    # Disable learning
   ```

## How It Works

### Vote Flow:
```
User clicks button
    ↓
Vote recorded in vote_tracking.json
    ↓
Button label updates (1/2, 2/2, etc.)
    ↓
Threshold reached (2 votes)?
    Yes → Delete message + Store in removed_entries.json + Clean up
    No → Wait for more votes
```

### Learning Flow:
```
Categorization request
    ↓
generate_enhanced_system_prompt()
    ↓
Load base prompt + recent removed entries
    ↓
Append negative examples
    ↓
Use enhanced prompt for categorization
    ↓
AI learns to avoid similar content
```

### Feedback Loop:
```
Content posted → Users vote as not valuable → Content removed
    ↓
Stored in removed_entries.json
    ↓
Added to system prompt as negative example
    ↓
AI categorizes similar content as 'ignore'
    ↓
Less low-value content posted → Better user experience
```

## Technical Details

### Vote Tracking Data Structure:
```json
{
  "discord_message_id_123": {
    "voters": ["user_id_1", "user_id_2"],
    "entry_id": "twitter_123456",
    "content": "full content...",
    "category": "crypto",
    "discord_channel_id": 1234567890,
    "timestamp": 1234567890.123
  }
}
```

### Removed Entries Data Structure:
```json
[
  {
    "entry_id": "twitter_123456",
    "content": "full content...",
    "category": "crypto",
    "removed_at": 1234567890.123,
    "voter_ids": ["user_id_1", "user_id_2"],
    "discord_message_id": 1234567890,
    "discord_channel_id": 9876543210,
    "source_url": "https://x.com/..."
  }
]
```

### Button Custom IDs:
- Perplexity: `perplexity_search:{entry_hash}`
- Not Valuable: `not_valuable:{entry_hash}`
- Hash: First 16 characters of MD5 hash of content

### Caching:
- Enhanced system prompt cached for 1 hour
- Reduces overhead of rebuilding prompt on every categorization
- Automatically refreshes when cache expires

## Performance Considerations

1. **Prompt Size:** System prompt grows with removed entries (max 20 by default)
   - Each example ~150 characters
   - Total addition: ~3000 characters
   - Minimal impact on Ollama performance

2. **Database Growth:** `removed_entries.json` grows over time
   - Automatic cleanup after 90 days (configurable)
   - Can be manually cleaned via `RemovedEntriesDB.cleanup_old_entries()`

3. **Vote Tracking:** `vote_tracking.json` cleaned automatically
   - Stale votes (48+ hours) removed
   - Minimal memory footprint

## Testing Checklist

✅ Button appears on Discord messages  
✅ First vote updates button to show "1/2"  
✅ Second unique vote triggers deletion  
✅ Same user cannot vote twice  
✅ Entry is removed from all databases  
✅ Entry is stored in removed_entries.json  
✅ System prompt includes recent removed entries  
✅ Dashboard shows removed entries  
✅ Restore functionality works  

## Future Enhancements (Optional)

1. **Vote Decay:** Votes expire after certain time period
2. **Admin Override:** Allow admins to remove with 1 vote
3. **Embedding Similarity:** Block similar content to removed entries
4. **Export/Import:** Bulk management of removed entries
5. **Analytics:** Track which categories get removed most
6. **User Reputation:** Weight votes based on user accuracy

## Troubleshooting

### Button doesn't appear:
- Check `NOT_VALUABLE_BUTTON_ENABLED` in config.py
- Ensure bot has database instance passed to DiscordPoster
- Check logs for initialization errors

### Votes not tracking:
- Verify `data/vote_tracking.json` exists and is writable
- Check VoteTracker initialization in logs
- Ensure Discord user IDs are being passed correctly

### Feedback learning not working:
- Check `FEEDBACK_LEARNING_ENABLED` in config.py
- Verify removed_entries.json has entries
- Check OllamaClient has removed_entries_db instance
- Review logs for enhanced prompt generation

### Dashboard page 404:
- Ensure removed.html template exists in templates/
- Check dashboard.py has `/removed` route
- Verify navigation link in base.html

## Security Considerations

1. **Vote Manipulation:** Unique user ID tracking prevents same user voting twice
2. **Database Integrity:** All file operations use try/except with logging
3. **Discord Permissions:** Uses interaction.user.id (server-verified)
4. **Dashboard Access:** Protected by HTTP Basic Auth (existing security)

## Conclusion

This implementation provides a complete feedback loop where user votes directly improve the bot's content filtering decisions. The system is:
- **Automated:** No manual intervention needed
- **Persistent:** Survives bot restarts
- **Scalable:** Handles thousands of entries efficiently
- **Configurable:** Easy to adjust behavior
- **Observable:** Full dashboard for monitoring

Users get better content, admins get insights, and the AI gets smarter over time! 🎉



