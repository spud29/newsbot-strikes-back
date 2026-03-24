# CLAUDE.md — AI Assistant Guide for newsbot-strikes-back

This file provides context for AI assistants (Claude Code and others) working on this codebase.

---

## Project Overview

**newsbot-strikes-back** is a Discord news aggregator bot that:
1. Polls Twitter (via RSS) and Telegram channels for content
2. Deduplicates stories using embedding-based cosine similarity
3. Categorizes content using a local Ollama LLM
4. Posts to the appropriate Discord channel with media attachments

---

## Repository Structure

```
newsbot-strikes-back/
├── main.py                 # NewsAggregatorBot orchestrator — start here
├── config.py               # All configuration and feature flags
├── run_bot.py              # Bot launcher (calls main.py)
├── utils.py                # Logging setup, retry_with_backoff decorator, helpers
│
├── db_connection.py        # Singleton SQLite connection + table creation
├── database.py             # Database class: processed_ids, embeddings, message_mapping
├── vote_tracker.py         # Tracks "Not Valuable" votes per Discord message
├── removed_entries.py      # Stores voted-out entries; provides feedback examples
├── retry_queue.py          # Retries gallery-dl failures across poll cycles
│
├── rss_poller.py           # Parses Twitter RSS feeds (feedparser)
├── telegram_poller.py      # Telethon client: real-time events + polling fallback
│
├── ollama_client.py        # Categorization, embeddings, similarity verification
├── perplexity_client.py    # "Get More Info" web search via Perplexity API
├── ocr_handler.py          # Tesseract OCR for image-only Telegram posts
├── media_handler.py        # gallery-dl (Twitter) + Telethon (Telegram) downloads
│
├── discord_poster.py       # Thin re-export shim — real code is split below:
├── discord_messaging.py    # DiscordPoster class: post_message, edit_message
├── discord_commands.py     # Context menu command registration & handlers
├── discord_ui.py           # RecategorizeModal (discord.py UI)
│
├── migrate_to_sqlite.py    # One-time migration from JSON files → SQLite
├── notion_uploader.py      # Optional Notion integration (unused in main flow)
│
├── data/                   # Runtime data (gitignored)
│   ├── newsbot.db          # SQLite database (auto-created)
│   ├── bot.pid             # PID file written by main.py on startup
│   ├── bot_stats.json
│   └── statistics.json
│
├── requirements.txt
├── README.md
└── SETUP.md
```

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.9+ |
| Discord API | discord.py ≥ 2.3.2 |
| Telegram API | Telethon ≥ 1.34.0 |
| RSS parsing | feedparser |
| Local AI | Ollama (HTTP API at `http://localhost:11434`) |
| Embeddings | `nomic-embed-text` via Ollama |
| Categorization LLM | `gpt-oss:20b` via Ollama |
| Web search AI | Perplexity API (`sonar-reasoning-pro`) |
| Database | SQLite (WAL mode, shared singleton connection) |
| OCR | pytesseract + Tesseract binary |
| Twitter media | gallery-dl CLI |
| Async HTTP | aiohttp |
| Numerics | numpy (cosine similarity) |
| Image processing | Pillow |
| Env management | python-dotenv |

---

## Environment Variables

Create a `.env` file in the project root (never commit it):

```env
# Required
DISCORD_TOKEN=your_discord_bot_token
TELEGRAM_API_ID=your_telegram_api_id
TELEGRAM_API_HASH=your_telegram_api_hash

# Optional — enables Perplexity "Get More Info" command
PERPLEXITY_API_KEY=your_perplexity_api_key

```

---

## Running the Project

### Prerequisites

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Ensure Ollama is running with required models
ollama pull gpt-oss:20b
ollama pull nomic-embed-text

# 3. Ensure Tesseract is installed (for OCR)
# Linux: sudo apt install tesseract-ocr
# macOS: brew install tesseract

# 4. Ensure gallery-dl is installed
pip install gallery-dl
```

### Start the Bot

```bash
python run_bot.py
# or equivalently
python main.py
```

### Logs

All output goes to `bot.log` (file) and stdout at `DEBUG` level. The log format is:
```
2024-01-01 12:00:00,000 - <module> - LEVEL - message
```

---

## Core Data Flow

```
Poll cycle (every 5 minutes, config.POLL_INTERVAL):
  1. retry_queue.get_entries_to_retry()     → failed entries from previous cycles
  2. rss_poller.poll_all_feeds()            → Twitter RSS entries
  3. telegram_poller.poll_all_channels()    → Telegram entries (catch-up)

  For each entry (oldest-first):
  4.  database.is_processed(entry_id)       → skip if seen
  5.  removed_entries_db.is_removed(...)    → skip if user-voted out
  6.  [image-only Telegram] → download media, run OCR
  7.  ollama.generate_embedding(content)    → nomic-embed-text
  8.  database.find_similar(embedding, DUPLICATE_THRESHOLD=0.95) → exact dup?
  9.  database.find_similar(embedding, SIMILARITY_THRESHOLD=0.60) → similar?
      → if similar: ollama.verify_similarity() LLM cross-check
  10. media_handler.download_*_media()      → download files to temp_media/
  11. ocr_handler                           → merge OCR text into content
  12. ollama.categorize(combined_content)   → returns category + reasoning
  13. [filters] PAUSE_MODE / duplicate / similar / newsworthiness / short video
  14. discord_poster.post_message()         → post to Discord channel
  15. database.mark_processed(entry_id)
  16. database.add_embedding()
  17. database.store_message_mapping()      → for edit sync & "Source" command
  18. media_handler.cleanup_entry_media()   → delete temp files

Real-time path (parallel to poll cycle):
  telegram_poller emits new messages → asyncio queue → process_telegram_queue()
  telegram_poller emits edited messages → asyncio queue → process_telegram_edits()
  discord context menu interactions → discord_commands.py handlers
```

---

## SQLite Database Schema

The database lives at `data/newsbot.db` (configured via `config.DB_PATH`). A single shared connection is managed by `db_connection.py` using a module-level singleton.

```sql
-- Tracks which entry IDs have been fully processed
CREATE TABLE processed_ids (
    entry_id TEXT PRIMARY KEY,
    timestamp REAL NOT NULL
);

-- Stores content embeddings for duplicate detection (loaded into memory at startup)
CREATE TABLE embeddings (
    content_hash TEXT PRIMARY KEY,
    embedding    TEXT NOT NULL,   -- JSON array of floats
    timestamp    REAL NOT NULL,
    preview      TEXT,
    content      TEXT,
    entry_id     TEXT
);

-- Maps source entries to their Discord messages (for edits, "Source" command)
CREATE TABLE message_mapping (
    entry_id            TEXT PRIMARY KEY,
    telegram_message_id INTEGER,
    discord_channel_id  INTEGER,
    discord_message_id  INTEGER,
    content             TEXT,
    source_url          TEXT,
    video_urls          TEXT,   -- JSON array
    category            TEXT,
    source_type         TEXT,   -- 'twitter' | 'telegram'
    reasoning           TEXT,   -- AI categorization reasoning
    timestamp           REAL
);
CREATE INDEX idx_message_mapping_discord_msg ON message_mapping(discord_message_id);

-- Tracks user votes on Discord messages
CREATE TABLE votes (
    discord_message_id TEXT PRIMARY KEY,
    voters             TEXT NOT NULL,   -- JSON array of user IDs
    timestamp          REAL,
    entry_id           TEXT,
    content            TEXT,
    category           TEXT,
    discord_channel_id INTEGER
);

-- Stores entries removed via community voting (used for AI feedback learning)
CREATE TABLE removed_entries (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id           TEXT,
    content            TEXT,
    category           TEXT,
    removed_at         REAL,
    voter_ids          TEXT,   -- JSON array
    discord_message_id INTEGER,
    discord_channel_id INTEGER,
    source_url         TEXT,
    embedding          TEXT    -- JSON array of floats
);
CREATE INDEX idx_removed_entries_entry_id ON removed_entries(entry_id);

-- gallery-dl failure queue; retried every 2 poll cycles
CREATE TABLE retry_queue (
    entry_id           TEXT PRIMARY KEY,
    entry_data         TEXT NOT NULL,   -- full entry dict as JSON
    retry_count        INTEGER DEFAULT 1,
    first_attempt_cycle INTEGER,
    last_attempt_cycle  INTEGER,
    reason             TEXT
);

-- Tracks the last Telegram message ID seen per channel (for polling catch-up)
CREATE TABLE last_message_ids (
    channel_name TEXT PRIMARY KEY,
    message_id   INTEGER NOT NULL
);
```

**WAL mode** is enabled. **Foreign keys** are enabled. All JSON columns store `json.dumps()` output and are deserialized with `json.loads()`.

**Embeddings are loaded into a Python `dict` in memory** (`Database._embeddings_cache`) at startup because SQLite cannot compute cosine similarity natively.

---

## Configuration Reference (`config.py`)

All runtime settings live in `config.py`. Edit this file (not `.env`) for anything non-secret.

| Variable | Default | Description |
|---|---|---|
| `PAUSE_MODE` | `True` | Routes ALL entries to `ignore` channel (silences everything) |
| `POLL_INTERVAL` | `300` | Seconds between polling cycles |
| `DB_RETENTION_HOURS` | `48` | How long processed entries are kept |
| `DUPLICATE_THRESHOLD` | `0.95` | Cosine similarity for exact duplicate |
| `SIMILARITY_THRESHOLD` | `0.60` | Cosine similarity triggering LLM cross-check |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `OLLAMA_CATEGORIZATION_MODEL` | `gpt-oss:20b` | Categorization model |
| `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Embedding model |
| `PERPLEXITY_MODEL` | `sonar-reasoning-pro` | Perplexity model |
| `OCR_ENABLED` | `True` | Tesseract OCR for image-only posts |
| `FEEDBACK_LEARNING_ENABLED` | `True` | Inject removed entries into LLM prompt |
| `FEEDBACK_EXAMPLES_COUNT` | `20` | How many negative examples to inject |
| `NOT_VALUABLE_VOTES_REQUIRED` | `2` | Votes needed to auto-remove a Discord post |
| `NEWSWORTHINESS_FILTER_ENABLED` | `False` | Optional filter by AI newsworthiness score |
| `NEWSWORTHINESS_THRESHOLD` | `7.0` | Minimum score (1–10) to post |
| `SHORT_VIDEO_FILTER_ENABLED` | `True` | Filter out short videos |
| `SHORT_VIDEO_THRESHOLD` | `60` | Minimum video duration in seconds |
| `DISCORD_FILE_SIZE_LIMIT_MB` | `25` | Skip media over this size |

### Discord Channels

```python
DISCORD_CHANNELS = {
    "crypto": ...,
    "politics": ...,
    "artificial intelligence": ...,
    "video games": ...,
    "sports": ...,
    "technology": ...,
    "music": ...,
    "fashion": ...,
    "pop culture": ...,
    "ignore": ...   # duplicates, low-quality, filtered content
}
```

### Valid Categories

`crypto`, `politics`, `stocks`, `artificial intelligence`, `video games`, `sports`, `food`, `technology`, `music`, `fashion`, `pop culture`, `ignore`

`VALID_CATEGORIES` is separate from `DISCORD_CHANNELS` so that disabled channels (e.g. `stocks` is commented out) don't cause valid AI responses to be rejected.

---

## Discord Context Menu Commands

Users **right-click a bot message → Apps** to access these:

| Command | Who | Description |
|---|---|---|
| **Get More Info** | Anyone | Spawns a thread; queries Perplexity for deeper context |
| **Not Valuable** | Anyone | Casts a vote; at `NOT_VALUABLE_VOTES_REQUIRED` votes the message is deleted and stored in `removed_entries` |
| **Re-categorize** | Allowlisted users only (`RECATEGORIZE_ALLOWED_USER_IDS`) | Opens a modal to move a post to a different channel |
| **Source** | Anyone | Ephemeral reply with original Telegram/Twitter URL |

All commands are registered and handled in `discord_commands.py`.

---

## Key Code Patterns

### Retry Decorator

```python
from utils import retry_with_backoff

@retry_with_backoff(max_retries=3, initial_delay=2)
def call_ollama(prompt):
    ...
```

Delays: 2 s → 4 s → 8 s. After exhausting retries, re-raises the last exception.

### Shared DB Connection

```python
from db_connection import get_db_connection

conn = get_db_connection()   # Returns the singleton; creates it on first call
rows = conn.execute("SELECT ...", (param,)).fetchall()
conn.commit()
```

Never create a new `sqlite3.connect()` elsewhere — always use `get_db_connection()`.

### Async + Blocking Ollama Calls

Ollama calls are blocking (synchronous `requests`). Wrap them in `asyncio.to_thread()` to avoid blocking the Discord event loop:

```python
category, reasoning = await asyncio.to_thread(self.ollama.categorize, content)
embedding = await asyncio.to_thread(self.ollama.generate_embedding, content)
```

### Race Condition Guard

`main.py` maintains `self._processing_lock: set` of entry IDs currently in flight. This prevents the real-time Telegram handler and polling cycle from both processing the same message:

```python
if entry_id in self._processing_lock:
    return False
self._processing_lock.add(entry_id)
try:
    ...
finally:
    self._processing_lock.discard(entry_id)
```

### Entry ID Format

- Twitter: `twitter_{tweet_status_id}` (e.g., `twitter_1867123456789`)
- Telegram: `telegram_{channel_name}_{message_id}` (e.g., `telegram_Fin_Watch_12345`)

---

## Module Responsibilities (Quick Reference)

| Module | Class / Key Functions | Responsibility |
|---|---|---|
| `main.py` | `NewsAggregatorBot` | Top-level orchestration, polling loop, entry pipeline |
| `config.py` | — | All settings; edit here for behaviour changes |
| `utils.py` | `logger`, `retry_with_backoff`, `cleanup_old_media_files` | Shared utilities |
| `db_connection.py` | `get_db_connection`, `close_db_connection` | Singleton SQLite connection |
| `database.py` | `Database` | processed_ids, embeddings (cosine search), message_mapping |
| `rss_poller.py` | `RSSPoller` | Fetch and parse Twitter RSS feeds |
| `telegram_poller.py` | `TelegramPoller` | Telethon client, real-time events, album grouping |
| `ollama_client.py` | `OllamaClient` | Categorize, embed, verify similarity, newsworthiness |
| `perplexity_client.py` | `PerplexityClient` | Web search for "Get More Info" threads |
| `ocr_handler.py` | `is_tradingview_chart_ocr`, `extract_text_from_images` | Tesseract OCR, TradingView chart filtering |
| `media_handler.py` | `MediaHandler`, `GalleryDlFailure` | gallery-dl (Twitter), Telethon download (Telegram) |
| `discord_poster.py` | — | Re-export shim → `discord_messaging.DiscordPoster` |
| `discord_messaging.py` | `DiscordPoster` | `post_message`, `edit_message`, Discord client lifecycle |
| `discord_commands.py` | `register_commands` | All four context menu commands |
| `discord_ui.py` | `RecategorizeModal` | Re-categorize modal dialog |
| `vote_tracker.py` | `VoteTracker` | Per-message vote counting |
| `removed_entries.py` | `RemovedEntriesDB` | Store & query voted-out entries; AI feedback |
| `retry_queue.py` | `RetryQueue` | gallery-dl failure retry across cycles |

---

## Common Development Tasks

### Add a New Content Category

1. Add to `VALID_CATEGORIES` list in `config.py`
2. Add to `DISCORD_CHANNELS` dict in `config.py` with the channel ID
3. Update `SYSTEM_PROMPT` in `config.py` with the category description and examples
4. Optionally update any hardcoded category lists in `discord_commands.py` (Re-categorize modal)

### Add a New RSS Feed

```python
# config.py
RSS_FEEDS = {
    "my_new_feed": "https://rss.app/feeds/XXXXXXXX.xml",
    ...
}
```

### Add a New Telegram Channel

```python
# config.py
TELEGRAM_CHANNELS = [
    "MyNewChannel",
    ...
]
```

### Temporarily Silence All Posting

Set `PAUSE_MODE = True` in `config.py`. All entries are processed but routed to the `ignore` channel. The original AI category is preserved in the reasoning field.

### Reset/Clear the Database

Delete `data/newsbot.db` and restart the bot (all state is lost).

---

## Files That Should Never Be Modified Directly

| File/Directory | Reason |
|---|---|
| `.env` | Secrets; gitignored |
| `data/newsbot.db` | Runtime database; managed by the application |
| `data/bot.pid` | Auto-written PID file |
| `bot.log` | Auto-generated log file |
| `temp_media/` | Ephemeral download directory |
| `*.session` | Telegram session files; contain credentials |

---

## Gitignore Summary

Key patterns in `.gitignore`:
- `.env` and all `*.env` variants
- `__pycache__/`, `*.pyc`, `*.pyo`
- `venv/`, `ENV/`, `env/`
- `*.log`, `bot.log`
- `newsbot.db*` (including WAL files)
- `data/*.json` (runtime state files)
- `temp_media/`
- `*.session*` (Telegram session)
- `twitter_cookies.txt`
- `.vscode/`, `.idea/`

---

## Documentation Index

The repo has many markdown files documenting individual features:

| File | Contents |
|---|---|
| `README.md` | User-facing overview, setup, troubleshooting |
| `SETUP.md` | Step-by-step installation guide |
| `PROJECT_SUMMARY.md` | Technical architecture summary |
| `DISCORD_SETUP.md` | Discord bot creation and permissions |
| `TWITTER_AUTHENTICATION_SETUP.md` | gallery-dl Twitter auth |
| `CONTEXT_MENU_MIGRATION.md` | Why buttons were replaced with context menus |
| `NOT_VALUABLE_BUTTON_IMPLEMENTATION.md` | Vote-to-remove feature |
| `THREAD_PRESERVATION_IMPLEMENTATION.md` | Threaded discussion feature |
| `RECATEGORIZE_IGNORE_IMPLEMENTATION.md` | Re-categorize command |
| `RETRY_MECHANISM.md` | gallery-dl retry queue |
| `URL_TRACKING_FEATURE.md` | Source URL tracking |

---

## Branch Conventions

- Active development happens on feature branches prefixed `claude/`
- The main branch is `main` (remote) / `master` (local)
- Commits should be descriptive (summarize the feature/fix, not the file changed)
