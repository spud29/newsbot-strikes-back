# Telegram Video Download Fix

## Issue
Videos from Telegram channels were being downloaded but not attached to Discord posts, even when they were under Discord's file size limit.

## Root Cause
The bot was using an **outdated 8MB file size limit** for Discord attachments. This was based on old Discord documentation, but Discord's actual limits are:
- **Free tier (basic servers): 25MB**
- **Level 2 boosted servers: 50MB**
- **Level 3 boosted servers: 100MB**

When a Telegram video was downloaded (e.g., 15.2MB), it would be skipped because it exceeded the 8MB limit, even though Discord would have accepted it.

## Evidence from Logs
```
2025-11-24 14:45:30,095 - utils - DEBUG - Downloaded Telegram media: 
C:\Users\spud9\OneDrive\Documents\newsbot strikes back\temp_media\telegram_21093\M_1EfYpYgoYndMnE.mp4

2025-11-24 14:45:30,095 - utils - DEBUG - Posting to Discord: 1 files, 1 videos

2025-11-24 14:45:30,095 - utils - WARNING - File too large (15939203 bytes), skipping: 
C:\Users\spud9\OneDrive\Documents\newsbot strikes back\temp_media\telegram_21093\M_1EfYpYgoYndMnE.mp4
```

The video (15.2MB) was downloaded successfully but rejected due to the artificial 8MB limit.

## Solution
1. **Updated the file size limit to 25MB** - This matches Discord's actual free tier limit
2. **Made the limit configurable** - Added `DISCORD_FILE_SIZE_LIMIT_MB` to `config.py`
3. **Improved logging** - Now shows file sizes in MB for easier understanding

## Changes Made

### config.py
Added new configuration option:
```python
# Discord file attachment size limit (in MB)
# Discord limits: 25MB (free), 50MB (level 2 boost), 100MB (level 3 boost)
DISCORD_FILE_SIZE_LIMIT_MB = 25
```

### discord_poster.py
Updated file size check to:
- Use the configurable limit from config.py
- Display file sizes in MB in warning messages
- Provide clearer error messages

## How to Customize
If your Discord server is boosted, you can increase the limit in `config.py`:
- For Level 2 boosted servers: `DISCORD_FILE_SIZE_LIMIT_MB = 50`
- For Level 3 boosted servers: `DISCORD_FILE_SIZE_LIMIT_MB = 100`

## Expected Behavior After Fix
- Videos from Telegram up to 25MB will now be attached to Discord posts (previously only up to 8MB)
- Videos larger than the configured limit will still be skipped with a clear warning message
- Smaller videos (already working) will continue to work as before

