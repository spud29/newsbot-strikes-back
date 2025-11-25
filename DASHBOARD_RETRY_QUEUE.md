# Dashboard - Retry Queue Monitoring

## Overview

The dashboard now includes a dedicated section for monitoring failed entries in the retry queue. This gives you real-time visibility into entries that failed gallery-dl extraction and are waiting for retry.

## Features

### 1. Retry Queue Status Card

A new collapsible card appears on the main dashboard **only when there are failed entries** waiting for retry:

```
┌─────────────────────────────────────────┐
│ 🔄 Retry Queue                    [3]   │
├─────────────────────────────────────────┤
│ Entries that failed gallery-dl          │
│ extraction are automatically queued     │
│ for retry (max 3 attempts).             │
│                                         │
│ Entry ID | Source | Attempts | Preview │
│ ─────────────────────────────────────── │
│ twitter_ | 🐦 acct| 1/3 ℹ️   | Text... │
│ ...                                     │
└─────────────────────────────────────────┘
```

### 2. Entry Details

Each retry queue entry displays:

- **Entry ID**: The full entry identifier
- **Source**: Source account/channel with icon (Twitter 🐦 or Telegram ✈️)
- **Attempts**: Visual badge showing retry count
  - 🔵 Blue (1/3): First attempt
  - ⚠️ Yellow (2/3): Second attempt  
  - 🔴 Red (3/3): Final attempt
- **Content Preview**: First 100 characters of content
- **Actions**:
  - 🔗 Link button (if source URL available)
  - 🗑️ Delete button (manually remove from queue)

### 3. Auto-Refresh

The retry queue automatically updates when you:
- Click the "Refresh" button
- The dashboard auto-refreshes (every 30 seconds by default)

### 4. Manual Controls

**Remove from Queue**: Click the 🗑️ button to manually remove an entry from the retry queue
- Useful if you know an entry will never succeed
- Prevents wasted retry attempts
- Requires confirmation before removal

## API Endpoints

### GET `/api/retry-queue`
Fetches retry queue details

**Response:**
```json
{
  "success": true,
  "stats": {
    "total_entries": 3,
    "by_retry_count": {
      "1": 2,
      "2": 1
    }
  },
  "entries": [
    {
      "entry_id": "twitter_1991818763537436982",
      "retry_count": 1,
      "source_type": "twitter",
      "source": "SolanaFloor",
      "content_preview": "Are digital asset treasury firms...",
      "link": "https://x.com/SolanaFloor/status/1991818763537436982"
    }
  ]
}
```

### DELETE `/api/retry-queue/{entry_id}`
Manually removes an entry from the retry queue

**Response:**
```json
{
  "success": true,
  "message": "Entry twitter_123 removed from retry queue"
}
```

## Integration with Bot

The dashboard reads from the same `data/retry_queue.json` file that the bot uses, so you get real-time visibility into:

- Which entries failed
- How many retry attempts have been made
- What content failed to extract
- When entries will be retried next

## Use Cases

### Monitoring Gallery-dl Health
If you see many entries in the retry queue, it might indicate:
- X.com rate limiting issues
- Network connectivity problems
- Gallery-dl configuration issues

### Debugging Failed Extractions
- Click the link button to view the original tweet/message
- Check if the content is still available
- Verify gallery-dl can access the URL

### Managing Queue Manually
- Remove entries that you know will never succeed
- Clear stuck entries to prevent wasted retry attempts
- Keep the queue clean and focused on recoverable failures

## Visual States

### Empty Queue (Normal)
- Retry queue section is hidden
- Indicates all extractions are successful

### Active Queue (Some Failures)
- Section appears with yellow/info styling
- Shows entries waiting for retry
- Auto-hides when queue is cleared

### High Retry Counts (Warning)
- Entries with 2-3 attempts show warning colors
- Indicates persistent extraction failures
- May need manual intervention

## Example Workflow

**Gallery-dl Failure Detected**
1. Bot encounters rate limit
2. Entry added to retry queue (attempt 1/3)
3. Dashboard shows entry in retry queue section

**Auto-Retry (10 minutes later)**  
4. Bot automatically retries entry
5. If successful: Entry posted to Discord, removed from queue
6. If failed again: Retry count incremented (2/3)

**Final Attempt (20 minutes later)**
7. Bot makes final retry attempt
8. If successful: Entry processed normally
9. If failed: Entry removed from queue, logged as unrecoverable

**Manual Intervention (Optional)**
- You can monitor progress in dashboard
- Remove hopeless entries manually
- Check source URLs to diagnose issues

