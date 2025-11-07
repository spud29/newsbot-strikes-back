# Quick Setup Guide

Follow these steps to get your Discord News Aggregator Bot up and running:

## 1. Install Python Dependencies

```bash
pip install -r requirements.txt
```

## 2. Install Ollama

1. Download and install Ollama from https://ollama.ai/
2. Pull the required models:

```bash
ollama pull gpt-oss:20b
ollama pull nomic-embed-text
```

3. Verify Ollama is running:
```bash
curl http://localhost:11434/api/tags
```

## 3. Get Discord Bot Token

1. Go to https://discord.com/developers/applications
2. Create a New Application
3. Go to the "Bot" section
4. Click "Add Bot"
5. Copy the token
6. Enable these Privileged Gateway Intents:
   - Message Content Intent
7. Invite bot to your server with these permissions:
   - Send Messages
   - Attach Files
   - Read Message History

## 4. Get Telegram API Credentials

1. Go to https://my.telegram.org/apps
2. Log in with your phone number
3. Create a new application
4. Copy the `api_id` and `api_hash`

## 5. Create .env File

Create a file named `.env` in the project root with your credentials:

```env
DISCORD_TOKEN=your_discord_bot_token_here
TELEGRAM_API_ID=your_telegram_api_id_here
TELEGRAM_API_HASH=your_telegram_api_hash_here
```

⚠️ **Important**: Never share or commit this file! It's already in `.gitignore`.

## 6. Verify Discord Channel IDs

Make sure the channel IDs in `config.py` match your Discord server:

```python
DISCORD_CHANNELS = {
    "crypto": 1317592423962251275,
    "news/politics": 1317592486927007784,
    # ... etc
}
```

To get a channel ID:
1. Enable Developer Mode in Discord (Settings > Advanced > Developer Mode)
2. Right-click on a channel
3. Click "Copy ID"

## 7. First Run

Before running the bot:
1. Make sure Ollama is running
2. Verify your `.env` file is configured
3. Check that Discord bot has proper permissions

Start the bot:
```bash
python main.py
```

### First Run Notes

- **Telegram Auth**: On first run, you may need to authenticate with Telegram by entering a code sent to your phone
- **Initial Processing**: The first cycle may take longer as it processes the backlog
- **Session File**: A `newsbot_session.session` file will be created for Telegram - keep this safe!

## 8. Monitoring

The bot will log to both console and `bot.log` file. Watch for:
- ✓ Successful initialization of all components
- ✓ Ollama health check passed
- ✓ Discord client logged in
- ✓ Telegram client started
- ✓ Regular polling cycles every 5 minutes

## Troubleshooting

### "Ollama health check failed"
- Ensure Ollama is running: `ollama serve`
- Check models are installed: `ollama list`

### "Discord client not ready"
- Verify your `DISCORD_TOKEN` in `.env`
- Check bot permissions in Discord server

### "Telegram auth failed"
- Verify `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` in `.env`
- Delete `newsbot_session.session` and re-authenticate

### "gallery-dl not found"
- Install gallery-dl: `pip install gallery-dl`
- Verify: `gallery-dl --version`

## What Happens Next?

Once running, every 5 minutes the bot will:
1. 🔍 Poll all RSS feeds and Telegram channels
2. 📥 Download full content and media
3. 🔎 Check for duplicates using AI embeddings
4. 🤖 Categorize content using Ollama
5. 📤 Post to appropriate Discord channels
6. 📊 Log statistics and summary

## Customization

Edit `config.py` to customize:
- Add/remove RSS feeds
- Add/remove Telegram channels
- Change categories and Discord channels
- Adjust duplicate detection threshold
- Modify system prompt for categorization
- Change polling interval

## Need Help?

Check `bot.log` for detailed debug information. Every operation is logged with context and timestamps.

