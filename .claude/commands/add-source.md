Help the user add a new content source (RSS feed or Telegram channel) to the bot.

Ask the user which type they want to add:

**For RSS feeds:**
1. Ask for a name (used as the dict key in `RSS_FEEDS`) and the RSS URL
2. Edit `config.py` to add the new entry to the `RSS_FEEDS` dictionary
3. Remind the user to restart the bot

**For Telegram channels:**
1. Ask for the channel username (without the @ symbol)
2. Edit `config.py` to add it to the `TELEGRAM_CHANNELS` list
3. Remind the user to restart the bot

In both cases, show the user the updated config section after editing.
