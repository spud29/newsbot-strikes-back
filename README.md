# Discord News Aggregator Bot

A sophisticated Discord bot that aggregates content from Twitter (via RSS feeds) and Telegram channels, categorizes them using local AI (Ollama), detects duplicates, and posts to appropriate Discord channels with full media support.

## Features

- 📰 **Multi-Source Aggregation**: Collects from Twitter RSS feeds and Telegram channels
- 🤖 **AI Categorization**: Uses Ollama with gpt-oss:20b for intelligent content categorization
- 🔍 **Duplicate Detection**: Employs embeddings and cosine similarity to prevent duplicate posts
- 🖼️ **Full Media Support**: Downloads and repost images/videos from both Twitter and Telegram
- 📊 **48-Hour Database**: Maintains a rolling window of processed content
- 🐛 **Comprehensive Logging**: Debug-level logging for easy troubleshooting

## Requirements

- Python 3.9+
- [Ollama](https://ollama.ai/) running locally with models:
  - `gpt-oss:20b` (for categorization)
  - `nomic-embed-text` (for embeddings)
- [gallery-dl](https://github.com/mikf/gallery-dl) (for Twitter media downloads)
- Discord bot token
- Telegram API credentials

## Installation

1. **Clone the repository** (or create the directory):
```bash
cd "C:\Users\spud9\OneDrive\Documents\newsbot strikes back"
```

2. **Install Python dependencies**:
```bash
pip install -r requirements.txt
```

3. **Install gallery-dl**:
```bash
pip install gallery-dl
```

4. **Install Ollama and pull required models**:
```bash
# Download from https://ollama.ai/
# Then pull models:
ollama pull gpt-oss:20b
ollama pull nomic-embed-text
```

5. **Create `.env` file** with your credentials:
```env
DISCORD_TOKEN=your_discord_bot_token_here
TELEGRAM_API_ID=your_telegram_api_id_here
TELEGRAM_API_HASH=your_telegram_api_hash_here
```

## Configuration

Edit `config.py` to customize:

- RSS feed URLs
- Discord channel IDs for each category
- Telegram channels to monitor
- Ollama models
- System prompt for categorization
- Duplicate detection threshold
- Polling interval

## Usage

1. **Ensure Ollama is running**:
```bash
# Ollama should be running on http://localhost:11434
```

2. **Start the bot**:
```bash
python main.py
```

The bot will:
- Poll RSS feeds and Telegram channels every 5 minutes
- Download full content and media
- Check for duplicates using embeddings
- Categorize using AI
- Post to appropriate Discord channels
- Log all activities to `bot.log` and console

## Web Dashboard (Optional)

A web-based monitoring and management dashboard is available:

**Features:**
- Real-time bot and Ollama status monitoring
- View recent activity and processing statistics
- Browse and filter error logs
- Test content categorization manually
- View configuration and manage database
- Search, export, and reset database entries

**Quick Start:**
```bash
# Install dashboard dependencies
pip install fastapi uvicorn jinja2 python-multipart

# Add to .env file:
# DASHBOARD_USERNAME=admin
# DASHBOARD_PASSWORD=your_secure_password

# Run dashboard (in separate terminal while bot is running)
uvicorn dashboard:app --reload --port 8000

# Access at: http://localhost:8000
```

**Documentation:**
- See `DASHBOARD.md` for full documentation
- See `DASHBOARD_QUICKSTART.md` for quick setup guide

## Categories

The bot can categorize content into:
- crypto
- news/politics
- stocks
- artificial intelligence
- video games
- sports
- food
- technology
- music
- fashion
- ignore (default for unclear content)

## Discord Channels

Configure your Discord channel IDs in `config.py`:
```python
DISCORD_CHANNELS = {
    "crypto": 1317592423962251275,
    "news/politics": 1317592486927007784,
    # ... etc
}
```

## RSS Feeds

Currently configured Twitter RSS feeds:
- unusual_whales
- dexerto_twitter
- solana_floor
- quiver_quant
- degenerate_news
- watcher_guru
- newswire

## Telegram Channels

Currently monitored Telegram channels:
- Fin_Watch
- news_crypto
- drops_analytics
- joescrypt
- unfolded
- unfolded_defi
- infinityhedge

## File Structure

```
newsbot strikes back/
├── main.py                     # Main bot orchestrator
├── config.py                   # Configuration and credentials
├── database.py                 # JSON database management
├── ollama_client.py           # AI categorization and embeddings
├── rss_poller.py              # RSS feed polling
├── telegram_poller.py         # Telegram channel monitoring
├── media_handler.py           # Media downloads
├── discord_poster.py          # Discord posting
├── utils.py                   # Utilities and logging
├── requirements.txt           # Python dependencies
├── README.md                  # This file
├── .env                       # Credentials (create this)
├── bot.log                    # Log file (auto-generated)
└── data/
    ├── processed_ids.json     # Processed entry IDs
    └── embeddings_cache.json  # Cached embeddings
```

## Logging

The bot logs to both console and `bot.log` file with debug-level detail:
- Feed/channel polling activities
- Entry processing steps
- Duplicate detection results
- Media downloads
- Discord posts
- Errors with full stack traces
- Cycle summary statistics

## Error Handling

All operations use retry with exponential backoff:
1. First attempt fails → wait 2s, retry
2. Second attempt fails → wait 4s, retry
3. Third attempt fails → wait 8s, retry
4. After 3 retries → log error, skip item, continue

## Database Management

- **processed_ids.json**: Tracks processed Twitter/Telegram IDs with timestamps
- **embeddings_cache.json**: Stores content embeddings for duplicate detection
- Both databases automatically clean up entries older than 48 hours

## Media Handling

**Twitter**:
- Uses gallery-dl to fetch full tweet text and all media
- Downloads images and videos
- Video URLs are hidden in Discord messages using markdown: `[.](video_url)`

**Telegram**:
- Uses Telethon to download images and videos
- Handles media albums as single posts
- Downloads all media types

## Duplicate Detection

Uses cosine similarity on embeddings:
- Threshold: 0.65 (configurable)
- Prevents posting the same story from multiple sources
- Logs similarity scores for debugging

## Tips

1. **First Run**: The bot may take longer on first run as it processes existing backlog
2. **Ollama Performance**: Ensure Ollama has enough resources (RAM/GPU) for smooth operation
3. **Discord File Size**: Files larger than 8MB are automatically skipped
4. **Rate Limits**: 5-minute polling interval prevents rate limiting
5. **Telegram Session**: First run will create `newsbot_session.session` file (keep this safe)

## Troubleshooting

**Bot won't start**:
- Check `.env` file has correct credentials
- Verify Ollama is running: `curl http://localhost:11434/api/tags`
- Ensure required models are pulled

**No posts appearing**:
- Check bot has permissions in Discord channels
- Review `bot.log` for errors
- Verify feed URLs are still active

**Duplicate posts**:
- Adjust `DUPLICATE_THRESHOLD` in `config.py`
- Check embeddings are being generated correctly in logs

**Media not downloading**:
- Ensure gallery-dl is installed: `gallery-dl --version`
- Check Telegram session is authenticated
- Verify sufficient disk space in temp folder

## License

This project is provided as-is for personal use.

## Support

Check logs in `bot.log` for detailed debugging information. All operations are logged with timestamps and context.

