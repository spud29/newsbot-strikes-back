# Thread Preservation Testing Guide

## Overview
This guide provides comprehensive testing scenarios for the thread preservation feature during re-categorization.

## Implementation Summary

### What Was Added
1. **`_extract_thread_perplexity_content()` method**: Extracts Perplexity AI responses from threads
2. **Thread detection in `recategorize_entry()`**: Checks for threads before moving messages
3. **Thread recreation logic**: Recreates threads with Perplexity content on new messages
4. **Enhanced success messages**: Indicates when threads are preserved

## Test Scenarios

### Scenario 1: Message Without Thread
**Setup:**
- Post a message to Discord (no Perplexity button clicked)
- Message has no thread

**Test Steps:**
1. Right-click the message
2. Select "Apps" → "Re-categorize"
3. Enter a new category
4. Submit

**Expected Result:**
- ✅ Message successfully moved to new category
- ✅ No thread created on new message
- ✅ Success message: "✅ Successfully re-categorized from **X** to **Y**!"

### Scenario 2: Message With Perplexity Thread
**Setup:**
- Post a message to Discord
- Click "Get More Info" context menu command
- Wait for Perplexity thread to be created with answer and citations

**Test Steps:**
1. Right-click the original message (the one with the thread)
2. Select "Apps" → "Re-categorize"
3. Enter a new category
4. Submit

**Expected Result:**
- ✅ Message successfully moved to new category
- ✅ Thread recreated on new message with same name
- ✅ Thread contains Perplexity answer embed
- ✅ Thread contains citations embed (if original had it)
- ✅ Success message: "✅ Successfully re-categorized from **X** to **Y**!\nNew message ID: [id]\n🧵 Thread with Perplexity content preserved!"

### Scenario 3: Message With Empty Thread
**Setup:**
- Post a message to Discord
- Manually create a thread on the message (no Perplexity content)

**Test Steps:**
1. Right-click the original message
2. Select "Apps" → "Re-categorize"
3. Enter a new category
4. Submit

**Expected Result:**
- ✅ Message successfully moved to new category
- ✅ No thread created on new message (since no Perplexity content)
- ✅ Log message: "Thread [id] exists but contains no Perplexity content"
- ✅ Success message indicates thread but may not mention preservation

### Scenario 4: Thread With Custom Content (Not Perplexity)
**Setup:**
- Post a message to Discord
- Create a thread and add custom messages (not Perplexity embeds)

**Test Steps:**
1. Right-click the original message
2. Select "Apps" → "Re-categorize"
3. Enter a new category
4. Submit

**Expected Result:**
- ✅ Message successfully moved to new category
- ✅ No thread created on new message (custom content not preserved)
- ✅ Log message: "No Perplexity content found in thread [id]"

### Scenario 5: Thread Extraction Fails
**Setup:**
- Simulate a scenario where thread history cannot be fetched (e.g., bot loses permissions temporarily)

**Test Steps:**
1. Attempt re-categorization
2. Monitor logs

**Expected Result:**
- ✅ Message still successfully moved to new category
- ✅ Error logged: "Error extracting thread content: [error]"
- ✅ Operation continues despite extraction failure

### Scenario 6: Thread Recreation Fails
**Setup:**
- Remove bot's thread creation permissions in the destination channel

**Test Steps:**
1. Re-categorize a message with Perplexity thread to that channel
2. Monitor logs

**Expected Result:**
- ✅ Message successfully moved to new category
- ✅ Error logged: "Bot lacks permission to create threads on new message"
- ✅ Operation completes successfully (thread just not recreated)

## Verification Checklist

After testing, verify the following:

- [ ] Messages without threads move normally (no errors)
- [ ] Messages with Perplexity threads have threads recreated
- [ ] Thread names match original names
- [ ] Answer embeds are preserved exactly
- [ ] Citations embeds are preserved (if present)
- [ ] Success messages correctly indicate thread preservation
- [ ] Thread extraction failures don't break re-categorization
- [ ] Thread recreation failures don't break re-categorization
- [ ] Logs provide useful debugging information
- [ ] No linter errors in `discord_poster.py`

## Log Messages to Monitor

### Debug Level
- `"Extracting Perplexity content from thread [id] (name: [name])"`
- `"Found Perplexity answer embed in thread [id]"`
- `"Found citations embed in thread [id]"`
- `"Scanned [count] messages in thread [id]"`
- `"No Perplexity content found in thread [id]"`
- `"Posted answer embed to new thread"`
- `"Posted citations embed to new thread"`

### Info Level
- `"Message [id] has a thread: [thread_id]"`
- `"Extracted Perplexity content from thread [id]"`
- `"Thread [id] exists but contains no Perplexity content"`
- `"Recreating thread on new message [id] with Perplexity content"`
- `"Created thread [id] on new message"`
- `"Successfully recreated thread with Perplexity content on message [id]"`

### Error Level
- `"Error extracting thread content: [error]"`
- `"Could not find new channel [id] to recreate thread"`
- `"Bot lacks permission to create threads on new message"`
- `"Error recreating thread: [error]"`

## Performance Considerations

- Thread extraction scans up to 50 messages in thread history
- Thread recreation adds ~1-2 seconds to re-categorization process
- No additional API calls if message has no thread

## Known Limitations

1. **Only Perplexity content is preserved** - Custom thread messages are not transferred
2. **Thread participants not preserved** - Thread membership resets on new thread
3. **Thread archive state not preserved** - New threads start unarchived
4. **50 message scan limit** - Very long threads may not be fully scanned

## Troubleshooting

### Thread Not Recreated
- Check logs for "No Perplexity content found" message
- Verify original thread contained Perplexity embeds
- Check bot has thread creation permissions in destination channel

### Embed Formatting Looks Different
- This should not happen - embeds are preserved exactly
- Report as bug if formatting differs

### Operation Times Out
- Thread extraction adds processing time
- May need to increase Discord interaction timeout
- Check if thread has excessive message count

## Code Locations

- **Helper method**: `discord_poster.py` lines ~193-258
- **Thread detection**: `discord_poster.py` lines ~923-940
- **Thread recreation**: `discord_poster.py` lines ~996-1033
- **Callback update**: `discord_poster.py` lines ~683-708

