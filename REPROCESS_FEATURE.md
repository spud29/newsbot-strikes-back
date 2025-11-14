# Reprocess Entry Feature

## Overview

Added a new "Reprocess Entry" feature to the dashboard that allows manually reprocessing entries that have already been processed. This solves the issue where resetting an entry wouldn't reprocess it because the bot only fetches new content from sources.

## The Problem We Solved

### Before:
1. User resets an entry from the database
2. Entry is removed from `processed_ids`, `embeddings`, and `message_mapping`
3. Entry is **never reprocessed** because:
   - Telegram only fetches messages newer than `last_message_ids`
   - RSS feeds only show recent items
   - Twitter only fetches newer tweets
   - The bot never goes back to old content

### After:
1. User searches for an entry in the dashboard
2. Clicks the "Reprocess" button
3. Entry is extracted from the database and reprocessed through the full pipeline
4. Posted to Discord again with a new entry ID

## How It Works

### Backend (`dashboard.py`)

Added new endpoint: `POST /api/database/reprocess/{entry_id}`

**Process:**
1. **Find Content**: Searches for content in `message_mapping` or `embeddings` cache
2. **Extract Content**: 
   - If found in `message_mapping`, uses the full content
   - If found in `embeddings` (with ID like `embedding_9b7247496a98`), extracts from preview
   - If embedding has a linked `entry_id`, attempts to get full content from that
3. **Process Pipeline**:
   - Categorize content using Ollama
   - Generate embedding
   - Check for duplicates (warns but doesn't block)
   - Post to Discord in the appropriate channel
4. **Store Results**:
   - Creates new entry ID: `manual_reprocess_{timestamp}`
   - Stores new embedding
   - Marks as processed
   - Creates message mapping

### Frontend (`templates/database.html`)

**Changes:**
- Added "Reprocess" button next to "Reset" button in search results
- Button group shows both actions side-by-side
- JavaScript handler `reprocessEntryFromSearch()` calls the API
- Shows confirmation dialog explaining what will happen
- Displays loading state during processing
- Shows detailed success message with category and Discord message ID

### UI Layout

```
Search Results:
┌─────────────────────────────────────────────────────┐
│ [Exact ID Match] telegram_news_crypto_20732        │
│                          [Reprocess] [Reset] ←──────│ NEW
│ Processed: 2025-01-08 12:34:56                     │
│ Source URL: https://twitter.com/...                │
│ Preview: Top Defi Projects By TVL...               │
└─────────────────────────────────────────────────────┘
```

## Usage

### 1. Search for Entry
Go to Dashboard → Database → Search for entry by ID or text content

### 2. Click Reprocess
Click the green "Reprocess" button next to any search result

### 3. Confirm
Confirm the action in the dialog:
```
Are you sure you want to reprocess and repost this entry?

Entry: embedding_9b7247496a98

This will:
- Extract the content from the database
- Categorize it again
- Post it to Discord again
- Create a new entry ID
```

### 4. Review Results
Success message shows:
```
✅ Entry successfully reprocessed!

Category: defi
Discord Message ID: 1234567890
New Entry ID: manual_reprocess_1704729600
⚠️ Similarity: 95.2% (if duplicate detected)
```

## Features

### ✅ Handles All Entry Types
- Regular entries (telegram_*, twitter_*)
- Embedding-only entries (embedding_*)
- Entries with full content in message_mapping
- Entries with only preview text in embeddings

### ✅ Smart Content Extraction
- Tries message_mapping first (full content)
- Falls back to embedding preview
- Follows entry_id links in embeddings to find full content
- Warns if content is truncated (< 100 chars)

### ✅ Full Pipeline Processing
- Categorizes with Ollama
- Generates new embedding
- Checks for duplicates (warns but doesn't block)
- Posts to correct Discord channel
- Stores with new entry ID

### ✅ Error Handling
- "No content found" if entry doesn't exist
- "No Discord channel configured" if category has no channel
- "Failed to post to Discord" if posting fails
- Detailed error messages for debugging

## Limitations

1. **Preview Text Only**: Old embeddings without linked `entry_id` may only have 100 chars of preview text
2. **No Media**: Reprocessed entries don't include media attachments (images/videos)
3. **Duplicate Warning**: Reprocessing will always trigger duplicate detection, but won't be blocked
4. **New Entry ID**: Creates a new entry ID rather than reusing the original

## Files Modified

1. **dashboard.py**
   - Added `DiscordPoster` import
   - Initialized `discord_poster` instance
   - Added `/api/database/reprocess/{entry_id}` endpoint

2. **templates/database.html**
   - Added "Reprocess" button to search results
   - Added `reprocessEntryFromSearch()` JavaScript function
   - Improved button layout with button groups

3. **REPROCESS_FEATURE.md** (this file)
   - Documentation for the feature

## Example Use Cases

### Use Case 1: Repost Deleted Discord Message
User accidentally deletes a Discord message → Search for entry → Click Reprocess → Message reposted

### Use Case 2: Test New Categorization
Changed categorization rules → Search old entry → Reprocess → See new category assignment

### Use Case 3: Recover Old Content
Want to repost old news → Search by keywords → Find entry → Reprocess → Posted again

### Use Case 4: Fix Miscategorized Entry
Entry was posted to wrong channel → Search entry → Reprocess → Posted to correct channel (if rules changed)

## Technical Notes

- Discord poster must be initialized and connected for reprocessing to work
- Reprocessing is async and may take 2-5 seconds (Ollama processing time)
- Each reprocess creates a unique entry ID to avoid conflicts
- Embeddings are regenerated, not reused, to ensure fresh duplicate checking
- Source URL is preserved if available in message_mapping

## Future Enhancements

Possible improvements:
- [ ] Option to preserve original entry ID
- [ ] Option to skip duplicate checking
- [ ] Support for reprocessing with media (if stored in temp_media)
- [ ] Bulk reprocess multiple entries
- [ ] Preview before posting
- [ ] Option to choose different category
- [ ] Ability to edit content before reprocessing

