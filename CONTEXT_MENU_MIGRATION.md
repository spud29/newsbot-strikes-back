# Discord Context Menu Commands Migration Guide

## Overview

The bot has been successfully upgraded from button-based UI to Discord context menu commands (also known as Message Commands). This provides a cleaner message appearance while maintaining all existing functionality.

## What Changed

### Before (Buttons)
- Every bot message had buttons attached below it
- "Get More Info" button (blue)
- "Not Valuable" button (red)
- Buttons cluttered the message appearance

### After (Context Menu Commands)
- Messages appear clean without any buttons
- Right-click on any bot message → Apps → Select command
- Three commands available:
  - **Get More Info** - Get additional context via Perplexity AI
  - **Not Valuable** - Vote to remove irrelevant content
  - **Re-categorize** - Move entry to a different category (restricted to authorized users)

## How to Use

### Get More Info Command
1. Right-click on a bot message
2. Hover over "Apps" in the context menu
3. Click "Get More Info"
4. The bot will create a thread with additional context from Perplexity AI
5. Citations are automatically included in the thread

### Not Valuable Command
1. Right-click on a bot message
2. Hover over "Apps" in the context menu
3. Click "Not Valuable"
4. You'll receive an ephemeral message (only you can see) confirming your vote
5. When the vote threshold is reached (default: 2 votes), the message is automatically removed
6. Removed content is used to improve future categorization

### Re-categorize Command
1. Right-click on a bot message
2. Hover over "Apps" in the context menu
3. Click "Re-categorize"
4. A modal will appear with a text input for the new category
5. Enter the category name (e.g., "crypto", "stocks", "technology")
6. Click "Submit"
7. You'll receive an ephemeral confirmation (only you can see)
8. The message will be deleted from the old channel and reposted in the new one
9. **Note**: This command is restricted to authorized users only (configured in `RECATEGORIZE_ALLOWED_USER_IDS`)

## Technical Details

### Architecture Changes
- **Upgraded from** `discord.Client` to `discord.Client` with `app_commands.CommandTree`
- **Removed** all Button View classes:
  - `CitationsView`
  - `PerplexitySearchView`
  - `NotValuableView`
  - `CombinedButtonView`
- **Added** context menu command registration in `_register_commands()`
- **Simplified** `post_message()` method (no longer creates views/buttons)

### Command Syncing
- Commands are automatically synced with Discord on bot startup
- You'll see a log message: `Successfully synced X application command(s)`
- Commands are global (available in all servers the bot is in)
- No bot reinvite needed if bot already has `applications.commands` scope

### Configuration
- Config variables remain the same for backward compatibility
- `PERPLEXITY_BUTTON_ENABLED` now controls the "Get More Info" command
- `NOT_VALUABLE_BUTTON_ENABLED` now controls the "Not Valuable" command
- `RECATEGORIZE_COMMAND_ENABLED` controls the "Re-categorize" command
- `RECATEGORIZE_ALLOWED_USER_IDS` defines which Discord user IDs can use the Re-categorize command
- Button appearance configs (label, emoji, style) are no longer used but kept for compatibility

## Benefits

1. **Cleaner UI** - Messages no longer have button clutter
2. **Same Functionality** - All features work exactly as before
3. **Better UX** - Context menus feel more native to Discord
4. **Persistent** - Commands work on old messages even after bot restart
5. **Flexible** - Can be invoked on any bot message at any time

## Testing Checklist

After starting the bot, verify:
1. ✅ Bot starts and syncs commands successfully
2. ✅ Right-click a bot message → "Apps" menu appears
3. ✅ "Get More Info", "Not Valuable", and "Re-categorize" commands are listed
4. ✅ "Get More Info" creates a thread with Perplexity response + citations
5. ✅ "Not Valuable" tracks votes and removes message at threshold
6. ✅ "Re-categorize" shows modal with category input (for authorized users only)
7. ✅ "Re-categorize" moves message to new category successfully
8. ✅ Unauthorized users see permission error when trying to re-categorize
9. ✅ Commands work on old messages (from before restart)

## Troubleshooting

### Commands don't appear in right-click menu
- Wait a few minutes after bot startup (Discord may take time to sync)
- Try restarting your Discord client
- Ensure bot has `applications.commands` scope (check bot invite URL)

### "This command only works on messages posted by the bot"
- This is expected - commands only work on the bot's own messages
- This prevents users from accidentally invoking commands on other users' messages

### Commands not syncing
- Check logs for "Successfully synced X application command(s)"
- If sync fails, check bot permissions in Discord Developer Portal
- Ensure bot token has correct permissions

### "You don't have permission to use this command" (Re-categorize)
- This is expected for non-authorized users
- Only users listed in `RECATEGORIZE_ALLOWED_USER_IDS` can use this command
- Add your Discord user ID to the config to grant permission

## Files Modified

1. **discord_poster.py** - Complete rewrite and updates
   - Removed all View classes (707 lines removed)
   - Added `app_commands.CommandTree` integration
   - Implemented three context menu commands:
     - "Get More Info" (Perplexity AI search)
     - "Not Valuable" (voting system)
     - "Re-categorize" (move entries between categories)
   - Removed text command handler for re-categorization
   - Simplified message posting

2. **config.py** - Documentation updates
   - Added comments explaining context menu usage
   - Noted which configs are no longer used
   - Updated re-categorize config for context menu approach
   - Preserved all configs for backward compatibility

## Rollback Instructions

If you need to rollback to the button-based UI:
1. Run: `git checkout HEAD~1 discord_poster.py config.py`
2. Restart the bot

However, the new context menu approach is recommended for better UX.

