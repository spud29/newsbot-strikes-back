# Telegram Album Fix

## Problem
Telegram messages with multiple images (albums) were only posting the first image to Discord instead of all images in the album.

## Root Cause
When Telegram posts an album with multiple images, each image arrives as a separate message with the same `grouped_id`. The bot has a `_group_albums()` method that properly groups these messages, but it was only being called during batch polling (`poll_all_channels()`), not during real-time message processing (`on_new_message()`).

In real-time mode, each message in an album was being processed immediately and individually, so they were never grouped together.

## Solution
Implemented an album buffering system for real-time messages:

1. **Album Buffer**: Added `album_buffer` dictionary to temporarily store messages with the same `grouped_id`
2. **Timer System**: Added `album_timers` to track timeout tasks for each album
3. **Buffering Logic**: When a message with a `grouped_id` is received:
   - Add it to the buffer for that `grouped_id`
   - Cancel any existing timer for that album
   - Start a new 2-second timer
4. **Flush Logic**: After 2 seconds of no new messages in an album:
   - Group all buffered messages using the existing `_group_albums()` method
   - Add the grouped entry to the processing queue

## Changes Made

### `telegram_poller.py`

1. **`__init__()`** - Added album buffer and timer tracking:
   ```python
   self.album_buffer = {}  # grouped_id -> list of parsed entries
   self.album_timers = {}  # grouped_id -> asyncio.Task for timeout
   ```

2. **`on_new_message()`** - Updated to buffer album messages:
   - Check if message has a `grouped_id`
   - If yes, buffer it instead of queuing immediately
   - If no, queue it immediately as before

3. **New method `_buffer_album_message()`**:
   - Adds message to buffer for its `grouped_id`
   - Cancels existing timer and starts new 2-second timer
   - Ensures all messages in an album are collected before processing

4. **New method `_flush_album_after_delay()`**:
   - Waits for delay (2 seconds)
   - Calls `_flush_album()` to process buffered messages
   - Handles cancellation if new messages arrive

5. **New method `_flush_album()`**:
   - Retrieves all buffered messages for an album
   - Groups them using existing `_group_albums()` method
   - Adds grouped entry to processing queue
   - Cleans up buffer and timer references

## Testing
To test this fix:
1. Post a Telegram message with 3+ images to one of your monitored channels
2. Verify that all images appear in the Discord post
3. Check the logs for messages like:
   - `Buffering album message with grouped_id X`
   - `Flushing album with N messages (grouped_id: X)`

## Technical Notes
- The 2-second delay is a balance between responsiveness and ensuring all messages arrive
- Telegram typically sends album messages within milliseconds of each other
- The timer resets with each new message in the album, ensuring we wait until all messages have arrived
- The same `_group_albums()` logic is used for both batch and real-time processing, ensuring consistency

