"""
Dexerto tweet pair merger.

Dexerto always posts stories as two tweets:
  Tweet 1: headline/summary text (no dexerto.com URL, usually has images)
  Tweet 2: short blurb + dexerto.com article link (a self-reply with no image)

Tweet 1 is buffered in a SQLite table (`dexerto_pending`). Tweet 2 is no longer
delivered by the rss.app RSS feed (the feed now surfaces only media tweets and
excludes text-only replies). Instead, `flush_stale()` fetches the conversation
via gallery-dl with `conversations=true` to find tweet 2 directly, then merges
its article URL into the headline entry before posting.

The `flush_stale()` method is called once per poll cycle during cleanup.
"""
import asyncio
import json
import re
import subprocess
import sys
import time

import config
from db_connection import get_db_connection, get_db_lock
from utils import logger

# Tweet 2 (the article reply) carries a dexerto.com URL. Deliberately NOT matching
# t.co: any link in a headline tweet becomes a t.co shortlink, so matching t.co here
# misread headlines-with-links as follow-ups. A follow-up whose link is still an
# unresolved t.co is caught later by flush_stale()'s reply_id conversation fetch.
DEXERTO_URL_PATTERN = re.compile(r'https?://(?:www\.)?dexerto\.com/\S+')


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

    def __init__(self, db, process_entry_fn, max_pending_hours: float = 1.0):
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
        with get_db_lock():
            rows = conn.execute(
                "SELECT entry_id, entry_json, buffered_at FROM dexerto_pending WHERE buffered_at < ?",
                (cutoff,)
            ).fetchall()

        for entry_id, entry_json, buffered_at in rows:
            age_hours = (time.time() - buffered_at) / 3600

            # An unparseable row can never succeed, and an uncaught exception here
            # used to abort poll_cycle at the same point every cycle — a single
            # corrupt row permanently halted all polling. Drop it instead.
            try:
                entry = json.loads(entry_json)
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"DexertoMerger: dropping corrupt pending row {entry_id}: {e}")
                with get_db_lock():
                    conn.execute("DELETE FROM dexerto_pending WHERE entry_id = ?", (entry_id,))
                    conn.commit()
                continue

            # Any other per-row failure leaves the row for the next cycle but must
            # not take down the loop (or the poll cycle awaiting it).
            try:
                # Try one final conversation fetch before posting alone.
                # The RSS feed no longer delivers tweet 2 (Dexerto's self-reply with the
                # article URL), but gallery-dl with conversations=true can fetch it directly.
                follow_up, tweet2_entry_id = await asyncio.to_thread(
                    self._find_follow_up_sync, entry
                )
                if follow_up:
                    logger.info(
                        f"DexertoMerger: found follow-up for {entry_id} at flush time — merging"
                    )
                    entry['dexerto_follow_up'] = follow_up
                    if tweet2_entry_id:
                        self._db.mark_processed(tweet2_entry_id)
                else:
                    logger.warning(
                        f"DexertoMerger: headline {entry_id} waited {age_hours:.1f}h with no "
                        f"follow-up tweet — posting alone"
                    )

                # Only remove from pending after the entry has been handled (posted or
                # marked processed by a filter). If process_entry fails transiently
                # (e.g. Ollama down) the row stays in pending so the next flush cycle
                # retries it — otherwise the entry would be silently lost.
                try:
                    success = await self._process_entry(entry)
                except Exception as e:
                    logger.error(f"DexertoMerger: error flushing stale entry {entry_id}: {e}", exc_info=True)
                    success = False

                if success or self._db.is_processed(entry_id):
                    with get_db_lock():
                        conn.execute("DELETE FROM dexerto_pending WHERE entry_id = ?", (entry_id,))
                        conn.commit()
                else:
                    logger.warning(
                        f"DexertoMerger: stale flush of {entry_id} did not complete — "
                        f"leaving in pending buffer to retry next cycle"
                    )
            except Exception as e:
                logger.error(
                    f"DexertoMerger: unexpected error flushing {entry_id}, "
                    f"leaving row for next cycle: {e}",
                    exc_info=True
                )

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    async def _handle_headline_tweet(self, entry: dict) -> bool:
        """Store tweet 1 (headline) in the pending table.

        Before buffering, check whether this entry is actually a reply to an
        already-pending headline. Dexerto sometimes posts the follow-up without
        a dexerto.com URL (e.g. a tinyurl.com link or plain explanatory text).
        The standard follow-up path (is_dexerto_follow_up_tweet) misses these,
        so we check the conversation here and merge if we find a parent.
        """
        entry_id = entry['id']

        # Check if this entry is a reply to a buffered headline.
        parent = await asyncio.to_thread(self._find_parent_headline_sync, entry)
        if parent:
            headline_entry_id, entry_json = parent['entry_id'], parent['entry_json']
            with get_db_lock():
                conn = get_db_connection()
                conn.execute(
                    "DELETE FROM dexerto_pending WHERE entry_id = ?", (headline_entry_id,)
                )
                conn.commit()

            headline_entry = json.loads(entry_json)
            headline_entry['dexerto_follow_up'] = re.sub(
                r'\n{2,}', '\n', entry.get('content', '').strip()
            )
            self._db.mark_processed(entry_id)
            logger.info(
                f"DexertoMerger: merging reply {entry_id} into headline {headline_entry_id} "
                f"(no dexerto.com URL in reply)\n"
                f"  reply content: {entry.get('content', '')[:120]}"
            )
            await self._process_entry(headline_entry)
            return True  # consumed

        # Not a reply to a buffered headline — buffer as a headline as normal.
        conn = get_db_connection()
        with get_db_lock():
            existing = conn.execute(
                "SELECT buffered_at FROM dexerto_pending WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            if existing:
                # Already pending — refresh entry_json but keep original buffered_at
                conn.execute(
                    "UPDATE dexerto_pending SET entry_json = ? WHERE entry_id = ?",
                    (json.dumps(entry), entry_id)
                )
                logger.debug(
                    f"DexertoMerger: refreshed pending headline {entry_id} "
                    f"(buffered {time.time() - existing[0]:.0f}s ago, waiting for follow-up)"
                )
            else:
                conn.execute(
                    "INSERT INTO dexerto_pending (entry_id, entry_json, buffered_at) VALUES (?, ?, ?)",
                    (entry_id, json.dumps(entry), time.time())
                )
                logger.info(f"DexertoMerger: buffered headline {entry_id} (waiting for follow-up tweet)")
            conn.commit()
        return True  # consumed — do NOT call process_entry for this entry yet

    def _find_parent_headline_sync(self, entry: dict) -> dict | None:
        """Check if this entry is a reply to a buffered headline.

        Fetches the conversation via gallery-dl and checks whether this entry's
        status_id appears as a reply_id in any buffered headline's conversation.
        Returns the matching buffered row dict, or None.
        """
        link = entry.get('link')
        if not link:
            return None

        try:
            this_sid = int(entry['id'].split('_')[1])
        except (ValueError, IndexError, AttributeError):
            return None

        cmd = [
            sys.executable, '-m', 'gallery_dl',
            '--config', config.GALLERY_DL_CONFIG,
            '--dump-json',
            '--no-download',
            '--option', 'extractor.twitter.conversations=true',
            link,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None
            items = json.loads(result.stdout.strip())
        except Exception:
            return None

        # Find buffered headlines whose status_id matches a reply_id in the
        # conversation items (meaning this entry replies to that headline).
        buffered = self._get_buffered_headlines()
        if not buffered:
            return None

        buffered_sids = {}
        for b in buffered:
            sid = self._status_id_of(b['entry_id'])
            if sid is not None:
                buffered_sids[sid] = b

        for item in items:
            if not (isinstance(item, list) and len(item) >= 2 and isinstance(item[1], dict)):
                continue
            d = item[1]
            try:
                reply_to = int(d.get('reply_id', 0))
            except (ValueError, TypeError):
                continue
            try:
                tweet_id = int(d.get('tweet_id', 0))
            except (ValueError, TypeError):
                continue
            if reply_to in buffered_sids and tweet_id == this_sid:
                return buffered_sids[reply_to]

        return None

    def _get_buffered_headlines(self) -> list:
        """Return all currently buffered headlines as dicts."""
        conn = get_db_connection()
        with get_db_lock():
            rows = conn.execute(
                "SELECT entry_id, entry_json, buffered_at FROM dexerto_pending"
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _status_id_of(entry_id):
        """Extract the integer Twitter status_id from a 'twitter_<id>' entry_id, or None."""
        try:
            return int(entry_id.rsplit('_', 1)[1])
        except (ValueError, IndexError, AttributeError):
            return None

    async def _handle_follow_up_tweet(self, entry: dict) -> bool:
        """Merge tweet 2 (blurb + URL) into the headline it replies to.

        Picks the pending headline with the greatest status_id still below the
        follow-up's — Dexerto tweets the headline first, then the article reply
        seconds later (tweet2.id > tweet1.id). Deterministic when several headlines
        are pending; the old "newest buffered_at" pick could merge a follow-up into
        the wrong story. Falls back to newest-buffered if snowflakes are missing.
        """
        follow_up_entry_id = entry['id']
        follow_up_content = re.sub(r'\n{2,}', '\n', entry.get('content', '').strip())
        follow_up_sid = self._status_id_of(follow_up_entry_id)

        conn = get_db_connection()
        with get_db_lock():
            rows = conn.execute(
                "SELECT entry_id, entry_json, buffered_at FROM dexerto_pending"
            ).fetchall()

        if not rows:
            # No pending headline. This means tweet 1 was already processed on a
            # previous cycle. Discard tweet 2 — it has no standalone value.
            logger.info(
                f"DexertoMerger: follow-up tweet {follow_up_entry_id} arrived but no "
                f"headline is pending — marking processed and discarding"
            )
            self._db.mark_processed(follow_up_entry_id)
            return True  # consumed

        match = None
        if follow_up_sid is not None:
            below = [
                r for r in rows
                if (sid := self._status_id_of(r['entry_id'])) is not None and sid < follow_up_sid
            ]
            if below:
                match = max(below, key=lambda r: self._status_id_of(r['entry_id']))
        if match is None:
            match = max(rows, key=lambda r: r['buffered_at'])

        headline_entry_id, entry_json = match['entry_id'], match['entry_json']

        # Remove matched headline from pending table
        with get_db_lock():
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
    # Conversation fetch
    # ------------------------------------------------------------------

    def _find_follow_up_sync(self, entry: dict) -> tuple:
        """
        Fetch the tweet conversation via gallery-dl and look for Dexerto's
        self-reply (tweet 2) containing a dexerto.com article URL.

        The rss.app RSS feed stopped delivering tweet 2 because it is a
        text-only reply with no image — the feed now surfaces only media
        tweets. This method bypasses the RSS feed by fetching the full
        conversation directly.

        Returns (follow_up_content, tweet2_entry_id) or (None, None).
        Blocking — call with asyncio.to_thread().
        """
        link = entry.get('link')
        if not link:
            logger.debug(f"DexertoMerger: no link in entry {entry.get('id')} — skipping conversation fetch")
            return None, None

        cmd = [
            sys.executable, '-m', 'gallery_dl',
            '--config', config.GALLERY_DL_CONFIG,
            '--dump-json',
            '--no-download',
            '--option', 'extractor.twitter.conversations=true',
            link,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
            )
            if result.returncode != 0 or not result.stdout.strip():
                logger.debug(
                    f"DexertoMerger: conversation fetch returned nothing for {entry.get('id')} "
                    f"(exit {result.returncode})"
                )
                return None, None
            items = json.loads(result.stdout.strip())
        except Exception as e:
            logger.warning(
                f"DexertoMerger: conversation fetch failed for {entry.get('id')}: {e}"
            )
            return None, None

        tweet1_id = int(entry['id'].split('_')[1])
        dexerto_user_id = 76766018  # Dexerto's stable Twitter user ID

        for item in items:
            if not (isinstance(item, list) and len(item) >= 2 and isinstance(item[1], dict)):
                continue
            d = item[1]
            try:
                reply_id = int(d.get('reply_id', 0))
            except (ValueError, TypeError):
                continue
            try:
                user_id = int(d.get('user', {}).get('id', 0))
            except (ValueError, TypeError):
                continue
            if (
                reply_id == tweet1_id
                and user_id == dexerto_user_id
                and 'dexerto.com' in d.get('content', '')
            ):
                follow_up = re.sub(r'\n{2,}', '\n', d['content'].strip())
                tweet2_id = d.get('tweet_id')
                tweet2_entry_id = f"twitter_{tweet2_id}" if tweet2_id else None
                logger.debug(
                    f"DexertoMerger: found tweet2 {tweet2_entry_id} in conversation: "
                    f"{follow_up[:80]}"
                )
                return follow_up, tweet2_entry_id

        logger.debug(
            f"DexertoMerger: conversation fetch got {len(items)} items for "
            f"{entry.get('id')} but none matched tweet2 criteria"
        )
        return None, None

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _ensure_table(self):
        """Create the dexerto_pending table if it doesn't exist."""
        conn = get_db_connection()
        with get_db_lock():
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dexerto_pending (
                    entry_id   TEXT PRIMARY KEY,
                    entry_json TEXT NOT NULL,
                    buffered_at REAL NOT NULL
                )
            """)
            conn.commit()
