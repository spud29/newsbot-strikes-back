# newsbot-strikes-back

A Discord news aggregator bot that polls Twitter (via RSS) and Telegram channels, deduplicates stories using AI embeddings, categorizes content with a local Ollama LLM, and posts to the appropriate Discord channel with full media support.

## Features

- **Multi-source aggregation** — Twitter RSS feeds and Telegram channels polled every 5 minutes
- **AI categorization** — local Ollama LLM (`gpt-oss:20b`) assigns each entry to one of 12 categories
- **Two-tier duplicate detection** — embedding cosine similarity with an LLM cross-check for borderline matches
- **Full media support** — images and videos downloaded from both Twitter (gallery-dl) and Telegram (Telethon)
- **OCR for image-only posts** — Tesseract extracts text from image-only Telegram entries
- **Community moderation** — right-click context menu commands for voting, re-categorizing, and sourcing
- **Feedback learning** — removed entries are fed back into the LLM prompt as negative examples
- **Content cleaning** — emoji removal, wire-prefix stripping (JUST IN, BREAKING, etc.), URL resolution, ALL CAPS fix
- **Retry queue** — gallery-dl failures are retried automatically across poll cycles
- **Perplexity integration** — "Get More Info" command spawns a thread with web search context

## Requirements

- Python 3.9+
- [Ollama](https://ollama.ai/) running locally with:
  - `gpt-oss:20b` (categorization)
  - `nomic-embed-text` (embeddings)
- [gallery-dl](https://github.com/mikf/gallery-dl) (Twitter media downloads)
- [Tesseract](https://github.com/tesseract-ocr/tesseract) (OCR, optional but recommended)
- Discord bot token
- Telegram API credentials
- Perplexity API key (optional — enables "Get More Info")

## Installation

1. **Install Python dependencies:**
```bash
pip install -r requirements.txt
```

2. **Install gallery-dl:**
```bash
pip install gallery-dl
```

3. **Pull Ollama models:**
```bash
ollama pull gpt-oss:20b
ollama pull nomic-embed-text
```

4. **Create a `.env` file** in the project root:
```env
DISCORD_TOKEN=your_discord_bot_token
TELEGRAM_API_ID=your_telegram_api_id
TELEGRAM_API_HASH=your_telegram_api_hash

# Optional
PERPLEXITY_API_KEY=your_perplexity_api_key
```

## Usage

```bash
python run_bot.py
```

The bot will:
1. Poll all RSS feeds and Telegram channels every 5 minutes
2. Clean content (HTML, emojis, wire prefixes, shortened URLs)
3. Generate an embedding and check for duplicates/similar stories
4. Download media (images, videos) via gallery-dl or Telethon
5. Run OCR on image-only posts
6. Categorize with the Ollama LLM
7. Post to the matching Discord channel with media attached
8. Store the embedding and message mapping in SQLite

## Configuration

All settings live in `config.py`. Key options:

| Setting | Default | Description |
|---|---|---|
| `PAUSE_MODE` | `True` | Routes everything to the `ignore` channel |
| `POLL_INTERVAL` | `300` | Seconds between poll cycles |
| `DUPLICATE_THRESHOLD` | `0.95` | Cosine similarity for exact duplicate rejection |
| `SIMILARITY_THRESHOLD` | `0.60` | Cosine similarity that triggers LLM cross-check |
| `DB_RETENTION_HOURS` | `48` | How long processed entries are retained |
| `DISCORD_FILE_SIZE_LIMIT_MB` | `25` | Max attachment size |
| `OCR_ENABLED` | `True` | Tesseract OCR for image-only posts |
| `FEEDBACK_LEARNING_ENABLED` | `True` | Inject removed entries into LLM prompt |
| `NOT_VALUABLE_VOTES_REQUIRED` | `2` | Votes needed to auto-remove a post |

### Logging

Default level is **INFO** (routing decisions + errors). App logs rotate at ~5 MB x 5 backups (`bot.log`). For temporary diagnostics set env `LOG_LEVEL=DEBUG` (or `LOG_LEVEL = "DEBUG"` in `config.py`) and restart.

## Categories

Content is categorized into one of:

`crypto` · `politics` · `stocks` · `artificial intelligence` · `video games` · `sports` · `food` · `science & technology` · `music` · `fashion` · `pop culture` · `software development` · `fitness` · `general news` · `ignore`

Each category maps to a Discord channel ID in `config.DISCORD_CHANNELS`.

## Discord Context Menu Commands

Users right-click a bot message and go to **Apps** to access:

| Command | Access | Description |
|---|---|---|
| **Get More Info** | Everyone | Spawns a thread with Perplexity web search results |
| **Not Valuable** | Everyone | Casts a removal vote; auto-deletes at threshold |
| **Re-categorize** | Allowlisted users | Moves a post to a different category channel |
| **Source** | Everyone | Shows the original Telegram/Twitter URL |

## Sources

**Twitter RSS feeds:** unusual_whales, dexerto_twitter, solana_floor, quiver_quant, degenerate_news, watcher_guru, newswire

**Telegram channels:** Fin_Watch, news_crypto, drops_analytics, joescrypt, unfolded, unfolded_defi, infinityhedge

Add new sources in `config.py` under `RSS_FEEDS` or `TELEGRAM_CHANNELS`.

## Project Structure

```
newsbot-strikes-back/
├── main.py                 # Bot orchestrator and entry processing pipeline
├── config.py               # All configuration and feature flags
├── run_bot.py              # Bot launcher
├── utils.py                # Logging, retry decorator, text cleaning helpers
├── db_connection.py        # Singleton SQLite connection (WAL mode)
├── database.py             # Processed IDs, embeddings cache, message mapping
├── removed_entries.py      # Voted-out entries store; feedback learning
├── retry_queue.py          # gallery-dl failure retry across poll cycles
├── rss_poller.py           # Twitter RSS feed parser (feedparser)
├── telegram_poller.py      # Telethon client: real-time events + polling
├── ollama_client.py        # Categorization, embeddings, similarity verification
├── perplexity_client.py    # "Get More Info" web search via Perplexity API
├── ocr_handler.py          # Tesseract OCR for image-only posts
├── media_handler.py        # gallery-dl (Twitter) + Telethon (Telegram) downloads
├── discord_poster.py       # Re-export shim for discord_messaging
├── discord_messaging.py    # DiscordPoster: post_message, edit_message
├── discord_commands.py     # Context menu command registration and handlers
├── discord_ui.py           # RecategorizeModal (discord.py UI)
├── migrate_to_sqlite.py    # One-time migration from legacy JSON → SQLite
├── requirements.txt
├── .env                    # Credentials (not committed)
└── data/
    └── newsbot.db          # SQLite database (auto-created)
```

## Database

SQLite database at `data/newsbot.db` with WAL mode enabled. Key tables:

- **processed_ids** — tracks which entry IDs have been fully processed
- **embeddings** — content embeddings for duplicate detection (loaded into memory at startup)
- **message_mapping** — maps source entries to Discord messages (for edits, Source command)
- **votes** — tracks user votes on Discord messages
- **removed_entries** — stores voted-out entries for feedback learning
- **retry_queue** — gallery-dl failures queued for retry
- **last_message_ids** — last Telegram message ID seen per channel

Entries older than `DB_RETENTION_HOURS` (48h) are automatically cleaned up.

## Content Cleaning Pipeline

Each entry passes through these text processing steps before posting:

1. HTML tag removal and entity decoding
2. Shortened URL resolution
3. Emoji removal
4. Corrupted emoji mark cleanup
5. **Wire-prefix stripping** — removes `JUST IN:`, `BREAKING:`, `NEW:`, etc.
6. Telegram formatting removal / Twitter attribution removal
7. ALL CAPS fix (optional, via Ollama)

## Duplicate Detection

Two-tier system using `nomic-embed-text` embeddings:

1. **Exact duplicate** (similarity >= 0.95) — silently dropped
2. **Similar story** (similarity >= 0.60) — LLM verifies whether it's truly the same story before dropping

This prevents both exact reposts and the same story from multiple sources, while allowing legitimately similar but distinct stories through.

## Error Handling

All external calls use `retry_with_backoff` (2s → 4s → 8s). gallery-dl failures are queued in the retry table and retried on subsequent poll cycles.

## Troubleshooting

- **Bot won't start** — check `.env` credentials; verify Ollama is running (`curl http://localhost:11434/api/tags`)
- **No posts appearing** — check `PAUSE_MODE` in config.py (routes everything to ignore when True); check bot permissions in Discord
- **Duplicate posts** — adjust `DUPLICATE_THRESHOLD` / `SIMILARITY_THRESHOLD` in config.py
- **Media not downloading** — verify `gallery-dl --version`; check Telegram session is authenticated
- **Logs** — all output goes to `bot.log` and stdout at DEBUG level
