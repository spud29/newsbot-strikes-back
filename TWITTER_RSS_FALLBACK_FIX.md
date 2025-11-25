# Twitter RSS Fallback Content Fix

## Problem

Twitter posts were showing raw HTML markup and attribution text in Discord:
- `<blockquote class="twitter-tweet">` tags
- `<p>` tags  
- Author/handle/date info like "— unusual_whales (@unusual_whales) Nov 25, 2025"

## Root Cause

The bot uses a two-tier system for extracting Twitter content:

1. **Primary**: gallery-dl extracts clean text directly from x.com URLs
2. **Fallback**: RSS feed content (used when gallery-dl fails)

### What Was Happening

1. **Gallery-dl was failing** for certain tweets with error:
   ```
   [twitter][info] No results for https://x.com/unusual_whales/status/...
   ```
   This is likely due to:
   - Twitter/X rate limiting
   - Deleted/restricted tweets
   - Authentication issues

2. **Exception handling was broken**: When gallery-dl raised `GalleryDlFailure`, the exception was being caught by the generic `except Exception` handler in `media_handler.py`, which swallowed the exception and returned the entry with empty media_files.

3. **Main processing continued** with the uncleaned RSS fallback content, which contained raw HTML and attribution text.

## Fixes Applied

### 1. Clean RSS Fallback Content (`rss_poller.py`)

Added proper cleaning to RSS content so even if it's used as fallback, it's clean:

```python
# Extract URLs from HTML anchors
content = extract_urls_from_html(content)

# Clean HTML tags (blockquote, p, etc.)
content = clean_text_content(content)

# Remove Twitter attribution (author/handle/date)
content = remove_twitter_attribution(content)
```

### 2. Fix Exception Handling (`media_handler.py`)

Modified exception handling to re-raise `GalleryDlFailure` so it propagates to the retry queue:

```python
except GalleryDlFailure:
    # Re-raise GalleryDlFailure so it can be handled by retry queue
    raise
except Exception as e:
    logger.error(f"Error downloading Twitter media: {e}")
    entry['media_files'] = []
    entry['ocr_text'] = ""
    return entry
```

Now when gallery-dl fails:
1. `GalleryDlFailure` is raised
2. It's re-raised by media_handler
3. The `@retry_with_backoff` decorator retries 3 times
4. After all retries fail, it re-raises to main.py
5. Main.py adds the entry to the retry queue instead of posting it with bad content

## Result

- RSS fallback content is now properly cleaned (removes HTML and attribution)
- Entries where gallery-dl fails are added to retry queue instead of being posted with bad content
- Next polling cycle will retry failed entries

## To Apply

Restart the bot to apply these changes:
```bash
# Stop the bot (from dashboard or manually)
# Then restart it
python run_bot.py
```

## Testing

Monitor the logs for:
- "gallery-dl failed to extract content from" - should add to retry queue
- "✓ Successfully extracted full text from x.com" - gallery-dl working correctly
- RSS content in Discord should no longer have HTML tags or attribution text

