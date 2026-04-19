"""
Dexerto tweet pair merger.

Dexerto always posts stories as two consecutive tweets:
  Tweet 1: headline/summary text (no dexerto.com URL)
  Tweet 2: short blurb + dexerto.com article link

This module buffers tweet 1 in a SQLite table (`dexerto_pending`) and waits
for tweet 2 to arrive — however long that takes, including across bot restarts.
When tweet 2 arrives it merges the follow-up content into the headline entry
and hands it to process_entry. The result is a single Discord post instead
of two.

The `flush_stale()` method is called once per poll cycle to post any headlines
that have been waiting longer than DEXERTO_PENDING_MAX_AGE_HOURS with no
matching follow-up (fallback for when tweet 2 never arrives).
"""
import json
import re
import time

from db_connection import get_db_connection
from utils import logger

DEXERTO_URL_PATTERN = re.compile(r'https?://(?:(?:www\.)?dexerto\.com|t\.co)/\S+')


def is_dexerto_follow_up_tweet(entry: dict) -> bool:
    """
    Return True if this entry is a Dexerto tweet 2 (follow-up blurb + article URL).

    Tweet 2 looks like:
        "Other law YouTubers also spoke out about Johnny Somali's sentence https://dexerto.com/..."
        "The full collaboration: https://dexerto.com/..."

    Tweet 1 is a pure headline with no dexerto.com URL in its content.
    """
    return bool(DEXERTO_URL_PATTERN.search(entry.get('content', '').strip()))


class DexertoMerger:
    """
    Buffers Dexerto headline tweets in a persistent SQLite table and waits
    for the matching follow-up tweet (blurb + article URL) before posting.

    Entries survive bot restarts — the DB holds the pending entry until
    tweet 2 shows up, no matter how long that takes.

    Usage in poll_cycle loop:
        consumed = await self.dexerto_merger.handle(entry)
        if consumed:
            continue
        success = await self.process_entry(entry)

    Call flush_stale() once per poll cycle during cleanup to evict entries
    that have been waiting too long with no follow-up.
    """

    def __init__(self, db, process_entry_fn, max_pending_hours: float = 4.0):
        """
        Args:
            db: Database instance (for mark_processed)
            process_entry_fn: Coroutine callable — async (entry: dict) -> bool
            max_pending_hours: Flush a pending headline alone after this many hours
                               if no follow-up tweet has arrived
        """
        self._db = db
        self._process_entry = process_entry_fn
        self._max_age = max_pending_hours * 3600
        self._ensure_table()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle(self, entry: dict) -> bool:
        """
        Evaluate one entry from the poll cycle.

        Returns:
            True  — entry consumed by the merger; caller should skip process_entry.
            False — not a dexerto_twitter entry; caller should process normally.
        """
        if entry.get('source') != 'dexerto_twitter':
            return False

        if is_dexerto_follow_up_tweet(entry):
            return await self._handle_follow_up_tweet(entry)
        else:
            return await self._handle_headline_tweet(entry)

    async def flush_stale(self):
        """
        Post any pending headlines that have been waiting longer than
        max_pending_hours with no matching follow-up tweet.

        Call this once per poll cycle during the cleanup phase.
        """
        conn = get_db_connection()
        cutoff = time.time() - self._max_age
        rows = conn.execute(
            "SELECT entry_id, entry_json, buffered_at FROM dexerto_pending WHERE buffered_at < ?",
            (cutoff,)
        ).fetchall()

        for entry_id, entry_json, buffered_at in rows:
            age_hours = (time.time() - buffered_at) / 3600
            logger.warning(
                f"DexertoMerger: headline {entry_id} waited {age_hours:.1f}h with no "
                f"follow-up tweet — posting alone"
            )
            # Only remove from pending after the entry has been handled (posted or
            # marked processed by a filter). If process_entry fails transiently
            # (e.g. Ollama down) the row stays in pending so the next flush cycle
            # retries it — otherwise the entry would be silently lost.
            try:
                success = await self._process_entry(json.loads(entry_json))
            except Exception as e:
                logger.error(f"DexertoMerger: error flushing stale entry {entry_id}: {e}", exc_info=True)
                success = False

            if success or self._db.is_processed(entry_id):
                conn.execute("DELETE FROM dexerto_pending WHERE entry_id = ?", (entry_id,))
                conn.commit()
            else:
                logger.warning(
                    f"DexertoMerger: stale flush of {entry_id} did not complete — "
                    f"leaving in pending buffer to retry next cycle"
                )

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    async def _handle_headline_tweet(self, entry: dict) -> bool:
        """Store tweet 1 (headline) in the pending table."""
        entry_id = entry['id']
        conn = get_db_connection()
        conn.execute(
            "INSERT OR REPLACE INTO dexerto_pending (entry_id, entry_json, buffered_at) VALUES (?, ?, ?)",
            (entry_id, json.dumps(entry), time.time())
        )
        conn.commit()
        logger.info(f"DexertoMerger: buffered headline {entry_id} (waiting for follow-up tweet)")
        return True  # consumed — do NOT call process_entry for this entry yet

    async def _handle_follow_up_tweet(self, entry: dict) -> bool:
        """Match tweet 2 (blurb + URL) to the most recent buffered headline."""
        follow_up_entry_id = entry['id']
        follow_up_content = entry.get('content', '').strip()

        conn = get_db_connection()
        row = conn.execute(
            "SELECT entry_id, entry_json FROM dexerto_pending ORDER BY buffered_at DESC LIMIT 1"
        ).fetchone()

        if not row:
            # No pending headline. This means tweet 1 was already processed on a
            # previous cycle. Discard tweet 2 — it has no standalone value.
            logger.info(
                f"DexertoMerger: follow-up tweet {follow_up_entry_id} arrived but no "
                f"headline is pending — marking processed and discarding"
            )
            self._db.mark_processed(follow_up_entry_id)
            return True  # consumed

        headline_entry_id, entry_json = row

        # Remove matched headline from pending table
        conn.execute("DELETE FROM dexerto_pending WHERE entry_id = ?", (headline_entry_id,))
        conn.commit()

        headline_entry = json.loads(entry_json)

        # Attach the full follow-up text so process_entry can append it after gallery-dl
        headline_entry['dexerto_follow_up'] = follow_up_content

        # Mark tweet 2 processed *before* calling process_entry so that if process_entry
        # fails and the entry lands in the retry queue, tweet 2 won't resurface as an
        # orphan follow-up tweet on the next cycle.
        self._db.mark_processed(follow_up_entry_id)

        logger.info(
            f"DexertoMerger: merging {headline_entry_id} + {follow_up_entry_id}\n"
            f"  follow-up: {follow_up_content[:120]}"
        )
        await self._process_entry(headline_entry)
        return True  # consumed

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _ensure_table(self):
        """Create the dexerto_pending table if it doesn't exist."""
        conn = get_db_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dexerto_pending (
                entry_id   TEXT PRIMARY KEY,
                entry_json TEXT NOT NULL,
                buffered_at REAL NOT NULL
            )
        """)
        conn.commit()
