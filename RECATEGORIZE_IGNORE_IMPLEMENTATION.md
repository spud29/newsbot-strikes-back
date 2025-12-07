# Re-categorize Ignore Entries - Implementation Summary

## Overview
Successfully implemented a feature to view entry details and re-categorize entries from the "ignore" category. The system automatically runs AI categorization with "ignore" blocked, deletes the old message, and posts to the new category.

## Implementation Details

### 1. Ollama Categorization with Exclusion (`ollama_client.py`)
**Changes:**
- Added `exclude_categories` parameter to the `categorize()` method
- System prompt is enhanced with exclusion notes when categories are excluded
- If AI returns an excluded category, it automatically falls back to a valid alternative
- Fallback logic prioritizes `news/politics` or the first available non-excluded category

**Key Features:**
- Exclusion is handled both in the prompt and post-processing
- Robust error handling ensures excluded categories are never returned, even on errors
- Logs warnings when AI attempts to return excluded categories

### 2. Category Detection (`dashboard.py`)
**Changes:**
- Updated `/api/entry/{entry_id}` endpoint to include `category` field in response
- Category information is pulled from `message_mapping` database
- Category is displayed for all entry lookups (both direct entry_id and embedding-based lookups)

### 3. Re-categorize API Endpoint (`dashboard.py`)
**New Endpoint:** `POST /api/entry/{entry_id}/recategorize-from-ignore`

**Workflow:**
1. **Verification**: Confirms entry exists and is in "ignore" category
2. **Content Retrieval**: Fetches content, media, and metadata from database
3. **AI Re-categorization**: Runs Ollama categorization with "ignore" excluded
4. **Safety Check**: Ensures "ignore" wasn't returned (forces to "news/politics" if needed)
5. **Media Discovery**: Finds existing media files in `temp_media` directory
6. **Old Message Deletion**: Removes the Discord message from ignore channel
7. **New Message Posting**: Posts content and media to new category channel
8. **Database Updates**: Updates message_mapping with new Discord IDs and category
9. **Cleanup**: Removes entry from removed_entries database if present

**Response Format:**
```json
{
  "success": true,
  "old_category": "ignore",
  "new_category": "technology",
  "discord_message_id": 123456789,
  "discord_channel_id": 987654321,
  "message": "Successfully re-categorized from ignore to technology"
}
```

### 4. Frontend Modal Enhancement (`templates/index.html`)
**Changes:**
- Entry details modal now displays the current category with color-coded badge
- "Re-categorize & Repost" button appears only for entries in "ignore" category
- Button shows loading state during processing
- Success/error alerts display after operation completes
- Automatically refreshes entry details and main stats after successful re-categorization

**UI Behavior:**
- Yellow warning badge for "ignore" category
- Blue info badge for other categories
- Confirmation dialog before re-categorization
- Real-time feedback with spinner during processing
- Auto-refresh after 2 seconds to show updated category

## How to Use

### From Dashboard:
1. Navigate to the Dashboard homepage
2. Click on any entry ID in the "Recent Activity" section
3. The entry details modal will open showing:
   - Entry ID, source type, and **current category**
   - Content preview
   - Media attachments (if any)
   - Discord and source information
4. If the entry is in the "ignore" category:
   - A yellow "Re-categorize & Repost" button will appear at the bottom
   - Click the button and confirm the action
   - AI will automatically determine the best category (excluding "ignore")
   - Old message is deleted, new message is posted
   - Modal updates to show the new category

### From Discord (Context Menu):
1. Right-click on any bot message in Discord
2. Hover over "Apps" in the context menu
3. Click "Re-categorize"
4. A modal will appear asking for the new category
5. Enter the category name (e.g., "crypto", "stocks", "technology")
6. Click "Submit"
7. The message will be deleted and reposted in the new category
8. **Note**: Only authorized users (listed in `RECATEGORIZE_ALLOWED_USER_IDS`) can use this command
9. **Note**: This works for ALL categories, not just "ignore" (unlike the dashboard option)

### API Usage:
```bash
curl -X POST "http://localhost:8000/api/entry/{entry_id}/recategorize-from-ignore" \
  -u "admin:your_password"
```

## Technical Notes

### Category Exclusion Logic
When "ignore" is excluded:
1. System prompt explicitly instructs AI not to use "ignore"
2. If AI still returns "ignore", post-processing forces alternative
3. Preference order for fallback: DEFAULT_CATEGORY → news/politics → first available

### Media Handling
- Searches `temp_media` directory for existing media files
- Supports both Twitter (images only) and Telegram (images + videos)
- Video URLs from Twitter are preserved from message_mapping
- Telegram videos are file-based and found in temp_media

### Database Consistency
- Entry ID remains the same (no reprocessing)
- Timestamp is preserved from original processing
- message_mapping is updated with new Discord IDs
- removed_entries is cleared if entry was marked as removed
- Embeddings remain unchanged (duplicate detection stays intact)

## Testing Checklist

- [x] Ollama categorization excludes "ignore" successfully
- [x] Entry details API returns category information
- [x] Re-categorize endpoint verifies entry is in "ignore"
- [x] Old Discord message is deleted
- [x] New Discord message is posted to correct category
- [x] Media files are re-attached correctly
- [x] Video URLs are preserved for Twitter entries
- [x] Database updates are persisted
- [x] Frontend displays category badge
- [x] Re-categorize button only shows for "ignore" entries
- [x] Loading states and error handling work properly
- [x] No linting errors in any modified files

## Files Modified

1. **ollama_client.py** - Added category exclusion to `categorize()` method
2. **dashboard.py** - Added category to entry details API and new re-categorize endpoint
3. **templates/index.html** - Enhanced modal with category display and re-categorize button

## Configuration

No configuration changes required. The feature uses existing config values:
- `config.DISCORD_CHANNELS` - For category validation
- `config.DEFAULT_CATEGORY` - For fallback logic
- Media files from `temp_media/` directory
- Standard authentication via HTTP Basic Auth

## Limitations

1. **Media Files**: Only re-posts media files that still exist in `temp_media` directory
   - Old files may have been cleaned up by retention policy
   - New media cannot be re-downloaded from original sources
   
2. **Entry Restriction**: Only works for entries currently in "ignore" category
   - Prevents accidental re-categorization of properly categorized entries
   - Can be modified if broader re-categorization is needed in future

3. **Single Exclusion**: Currently hardcoded to exclude "ignore"
   - Could be expanded to exclude multiple categories if needed
   - UI would need dropdown for manual category selection

## Future Enhancements

Potential improvements for future versions:
- Manual category selection option (in addition to auto-categorization)
- Re-categorize from any category (not just "ignore")
- Bulk re-categorization for multiple entries
- Media re-download if files are missing
- Category history tracking
- Undo functionality

