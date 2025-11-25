# Gallery-dl Retry Mechanism

## Overview

The bot now has an intelligent retry system for handling temporary gallery-dl failures when extracting Twitter content. Instead of falling back to degraded RSS content (which includes `pic.twitter.com` URLs in the text), the bot will queue failed entries for retry in future poll cycles.

## How It Works

### 1. **Failure Detection**
When gallery-dl fails to extract tweet content, a `GalleryDlFailure` exception is raised instead of falling back to RSS content.

### 2. **Retry Queue**
Failed entries are added to a retry queue (`data/retry_queue.json`) with metadata:
- Entry data
- Retry attempt count
- First and last attempt timestamps
- Failure reason

### 3. **Retry Strategy**
- **Delay**: Entries wait **2 poll cycles** before retry (typically 10 minutes with default 5-minute polling)
- **Max Retries**: Each entry gets **3 retry attempts** maximum
- **Priority**: Retry entries are processed **first** in each poll cycle

### 4. **Automatic Cleanup**
- Entries are removed after successful processing
- Entries exceeding max retries are automatically removed
- Entries older than 24 hours are expired and removed

## Configuration

You can adjust retry behavior in `main.py`:

```python
self.retry_queue = RetryQueue(
    max_retries=3,           # Maximum retry attempts
    retry_delay_cycles=2     # Wait 2 cycles before retry
)
```

## Why This Helps

### Common Gallery-dl Failure Causes:
1. **X.com Rate Limiting** - Temporary blocks on guest token requests
2. **Network Timeouts** - Temporary connectivity issues
3. **API Instability** - X.com's guest API can be unreliable

### Benefits:
- ✅ **Better Content Quality** - Gets full tweet text instead of RSS fallback
- ✅ **No Media URLs in Text** - Properly downloads media instead of showing `pic.twitter.com/...`
- ✅ **Automatic Recovery** - Handles temporary failures gracefully
- ✅ **Smart Backoff** - Waits before retrying to avoid rate limits

## Monitoring

The bot logs retry queue status each cycle:

```
Retry Queue: 3 entries waiting for retry
```

And logs when entries are added/removed:

```
Entry added to retry queue (attempt 1/3): twitter_1991818763537436982
✓ Entry successfully processed after 2 retry(ies): twitter_1991818763537436982
```

## Example Scenario

**Tweet Posted:** 4:39 AM, Nov 21st

**Poll Cycle 1 (4:40 AM):**
- Gallery-dl fails (X.com rate limit)
- Entry added to retry queue (attempt 1/3)
- Waits 2 cycles (10 minutes)

**Poll Cycle 2-3 (4:45 AM, 4:50 AM):**
- Entry waits in queue

**Poll Cycle 4 (4:55 AM):**
- Entry eligible for retry
- Gallery-dl succeeds
- Tweet posted with full content + images
- Entry removed from retry queue

## Files

- `retry_queue.py` - Retry queue manager
- `data/retry_queue.json` - Persistent retry queue storage
- `media_handler.py` - Raises `GalleryDlFailure` exception
- `main.py` - Catches exception and manages retry flow

