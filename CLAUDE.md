# CLAUDE.md

This project is a Discord news aggregator bot. It polls Twitter RSS feeds and Telegram channels, deduplicates with embeddings, categorizes with a local Ollama LLM, and posts to Discord. All configuration lives in `config.py`; secrets live in `.env`.

---

## Project Location

Project may live in OneDrive folders, not just Documents/home. Common project root to check:
- NewsBot / "Newsbot Strikes Back"
- Dexerto / Discord bots

Always verify working directory and confirm project identity before making changes.

---

## Investigation Before Editing

When the user reports a bug, DO NOT make code edits until you have:
1. Read the actual error/log message they're referencing
2. Traced the specific code path causing THEIR reported symptom
3. Confirmed your diagnosis matches their described issue (not a tangentially related bug)

If you spot other bugs during investigation, mention them but do not fix them without asking.

---

## Bot Restart Protocol

Before restarting any bot (`main.py`, `bot.py`, `run_bot.py`), ALWAYS:
1. List ALL running Python processes matching the bot name (e.g., `tasklist | findstr python` or `ps aux | grep`)
2. Kill ALL stale/duplicate instances before starting a new one
3. Verify only one instance is running after restart
4. Never use broad `taskkill` that could affect unrelated Python processes

---

## Git Hygiene

- **Commit after each meaningful change** — don't let modified files pile up across multiple features or refactors. One logical unit of work = one commit.
- **Stage intentionally** — never `git add .` blindly. Skip `bot.log`, `.env`, screenshot files, and anything in `data/`.
- **Write descriptive commit messages** — lead with what changed and why, not just filenames.
- **New files need commits too** — untracked `.py` files are easy to forget; check `git status` before considering work "done".

---

## Code Constraints

These are non-obvious rules that must be followed:

**Database connection** — always use the singleton, never create a new connection directly:
```python
from db_connection import get_db_connection
conn = get_db_connection()
```

**Ollama calls are blocking** — always wrap them in `asyncio.to_thread()` to avoid blocking the Discord event loop:
```python
category, reasoning = await asyncio.to_thread(self.ollama.categorize, content)
embedding = await asyncio.to_thread(self.ollama.generate_embedding, content)
```

**Start the bot with `python main.py`** — `run_bot.py` is a thin wrapper but `main.py` is the canonical entry point.

---

## Files Never to Modify

| File/Directory | Reason |
|---|---|
| `.env` | Secrets; gitignored |
| `data/newsbot.db` | Runtime database; managed by the application |
| `data/bot.pid` | Auto-written PID file |
| `bot.log` | Auto-generated log file |
| `temp_media/` | Ephemeral download directory |
| `*.session` | Telegram session files; contain credentials |

---

## Common Tasks

### Add a new content category
1. Add to `VALID_CATEGORIES` in `config.py`
2. Add to `DISCORD_CHANNELS` in `config.py` with the channel ID
3. Update `SYSTEM_PROMPT` in `config.py` with a description and examples
4. Update the category list in `discord_commands.py` (Re-categorize modal)

### Add a new RSS feed
```python
# config.py
RSS_FEEDS = {
    "my_new_feed": "https://rss.app/feeds/XXXXXXXX.xml",
}
```

### Add a new Telegram channel
```python
# config.py
TELEGRAM_CHANNELS = [
    "MyNewChannel",
]
```

### Silence all posting temporarily
Set `PAUSE_MODE = True` in `config.py`. Entries are still processed but routed to the `ignore` channel; the original AI category is preserved in the reasoning field.

### Reset the database
Delete `data/newsbot.db` and restart. All processed entry history and embeddings are lost.

---

## Branch Conventions

- Feature branches are prefixed `claude/`
- Main branch is `main`
