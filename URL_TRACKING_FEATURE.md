# URL Tracking Feature

## Overview
The bot now tracks and stores the original source URL (Twitter/X.com link, etc.) for all processed entries.

## What Changed

### 1. Database Storage (`database.py`)
- Updated `store_message_mapping()` to accept and store a `source_url` parameter
- All entries now save their original source URL for later retrieval

### 2. Entry Processing (`main.py`)
- **Twitter/RSS entries**: Automatically extracts and stores the `x.com` or `twitter.com` URL from the RSS feed
- **Telegram entries**: Automatically constructs `t.me` URLs in the format `https://t.me/channel_name/message_id`
- URL tracking works for ALL entries (both Twitter and Telegram)
- Edit handler preserves source URLs when updating content
- Handles channel names with or without `@` prefix

### 3. Dashboard Search (`dashboard.py` & `templates/database.html`)
- Search results now display the source URL as a clickable link
- Shows an external link icon for easy identification
- URLs open in a new tab for convenience

## How to Use

### Finding Source URLs (Twitter & Telegram)
1. Go to the Database Tools page in your dashboard
2. Search for an entry by:
   - Entry ID (e.g., "twitter_1234567890" or "telegram_drops_analytics_7556")
   - Partial ID (e.g., "twitter" or "telegram")
   - Text content (e.g., "Ukraine" or any word from the message)
3. The search results will show:
   - Entry ID
   - Processing timestamp
   - **Source URL** (clickable link to the original tweet or Telegram message)
   - Content preview

### Example Search Results

**Twitter Entry:**
```
┌─────────────────────────────────────────────────────┐
│ [Exact ID Match] twitter_1234567890                 │
│ Processed: 2025-11-09 10:30:45                     │
│ Source URL: https://x.com/user/status/1234567890  │↗
│ Preview: Breaking news about...                    │
└─────────────────────────────────────────────────────┘
```

**Telegram Entry:**
```
┌─────────────────────────────────────────────────────┐
│ [Content Match] telegram_drops_analytics_7556       │
│ Processed: 2025-11-09 11:15:22                     │
│ Source URL: https://t.me/drops_analytics/7556     │↗
│ Preview: Market update for...                      │
└─────────────────────────────────────────────────────┘
```

## Data Format

The `message_mapping.json` now stores:

**Twitter Entry:**
```json
{
  "twitter_1234567890": {
    "telegram_message_id": 0,
    "discord_channel_id": 1234567890,
    "discord_message_id": 9876543210,
    "content": "Tweet text content...",
    "source_url": "https://x.com/username/status/1234567890",
    "timestamp": 1699456789.123
  }
}
```

**Telegram Entry:**
```json
{
  "telegram_drops_analytics_7556": {
    "telegram_message_id": 7556,
    "discord_channel_id": 1234567890,
    "discord_message_id": 9876543210,
    "content": "Telegram message content...",
    "source_url": "https://t.me/drops_analytics/7556",
    "timestamp": 1699456789.123
  }
}
```

## Notes

- **Existing entries**: Only new entries processed after this update will have URLs stored
- **Backward compatible**: Old entries without URLs will still work (URL field will be empty)
- **Twitter/RSS**: URLs are automatically extracted from the RSS feed's `link` field
- **Telegram**: URLs are automatically constructed from channel name and message ID in the format `https://t.me/{channel}/{message_id}`
- **Channel names**: Automatically strips `@` prefix if present to ensure proper URL formatting

## Benefits

1. **Easy reference**: Quickly find the original tweet or Telegram message from your Discord posts
2. **Verification**: Check the original source if needed  
3. **Troubleshooting**: Debug which messages are being processed
4. **Audit trail**: Keep track of where content came from
5. **Direct access**: Click the URL to open the original message in your browser or app

