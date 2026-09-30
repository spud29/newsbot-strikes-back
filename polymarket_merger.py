"""
Polymarket tweet pair merger.

Polymarket posts stories as two tweets:
  Tweet 1: headline/summary of a prediction market event (no poly.market URL, 
           usually has an embedded image)
  Tweet 2: self-reply with the odds/betting line + poly.market URL + embedded card

Tweet 1 is buffered in a SQLite table (`polymarket_pending`). Tweet 2 is the
follow-up reply. When tweet 2 arrives (detected by the poly.market URL in its
content), it's merged into the headline entry. If tweet 2 never arrives within
max_pending_hours, the stale headline is flushed via gallery-dl conversation
fetch or posted alone.

Usage is identical to DexertoMerger.
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

# Polymarket's short URL domain and full domain for follow-up tweets.
# Matches both "poly.market/..." and "polymarket.com/..."
POLYMARKET_URL_PATTERN = re.compile(
    r'https?://(?:www\.)?(?:poly\.market|polymarket\.com)/\S+'
)

# Polymarket's stable Twitter user ID (from gallery-dl extraction)
POLYMARKET_USER_ID = 1261335549215989760


def is_polymarket_follow_up_tweet(entry: dict) -> bool:
    """
    Return True if this entry is a Polymarket tweet 2 (self-reply with odds
    and a poly.market / polymarket.com URL).

    Tweet 2 looks like:
        "R to @Polymarket: 59% chance El-Sayed defeats Mike Rogers.
         https://poly.market/d1MRRVL"

    Tweet 1 is the news/headline tweet with NO poly.market URL in its content.

    Retweets ("RT by @Polymarket: ...") can also carry poly.market URLs but are
    NOT follow-ups — they're excluded by requiring the reply marker in the title.
    """
    content = entry.get('content', '').strip()
    if not POLYMARKET_URL_PATTERN.search(content):
        return False
    # Reply marker distinguishes a genuine self-reply from a retweet.
    title = entry.get('title', '')
    return 'R to @Polymarket' in title


def is_polymarket_retweet(entry: dict) -> bool:
    """True if this entry is a retweet ("RT by @Polymarket: ...")."""
    return entry.get('title', '').startswith('RT')


def clean_polymarket_follow_up(text: str) -> str:
    """
    Trim a follow-up tweet to its odds line + poly.market URL.

    The RSS description carries card junk after the URL:
        "59% chance El-Sayed defeats Mike Rogers.
         https://poly.market/d1MRRVL
         Link
         Michigan Senate Election Winner
         $194,513 Vol...."
    Keep only the text through the URL, and drop any "R to @Polymarket:" prefix.

    Returns empty string if no poly.market URL is found — the caller treats
    this as "no valid follow-up" and posts the headline alone.
    """
    text = re.sub(r'^R to @Polymarket:\s*', '', text.strip())
    m = POLYMARKET_URL_PATTERN.search(text)
    if m:
        text = text[:m.end()].rstrip()
    else:
        # No poly.market URL — this isn't a valid Polymarket self-reply.
        # Return empty so the merger skips appending card junk to the headline.
        return ""
    return re.sub(r'\n{2,}', '\n', text).strip()


class PolymarketMerger:
    """
    Buffers Polymarket headline tweets in a persistent SQLite table and waits
    for the matching follow-up tweet (odds + poly.market URL) before posting.

    Entries survive bot restarts — the DB holds the pending entry until
    tweet 2 shows up, no matter how long that takes.

    Usage in poll_cycle loop:
        consumed = await self.polymarket_merger.handle(entry)
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
            False — not a polymarket entry (or a retweet / self-contained headline);
                    caller should process normally.
        """
        if entry.get('source') != 'polymarket':
            return False

        # Retweets (RT by @Polymarket) pass through untouched — they're not part
        # of the headline+reply pair pattern.
        if is_polymarket_retweet(entry):
            return False

        if is_polymarket_follow_up_tweet(entry):
            return await self._handle_follow_up_tweet(entry)
        elif self._is_self_contained_headline(entry):
            # Headline already carries a poly.market URL (embedded odds or
            # 🚨 NEW POLYMARKET: posts). No follow-up reply will ever arrive —
            # let the caller process it normally instead of buffering.
            logger.debug(
                f"PolymarketMerger: self-contained headline {entry['id']} "
                f"(poly.market URL in content) — not buffering"
            )
            return False
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
                "SELECT entry_id, entry_json, buffered_at FROM polymarket_pending WHERE buffered_at < ?",
                (cutoff,)
            ).fetchall()

        for entry_id, entry_json, buffered_at in rows:
            age_hours = (time.time() - buffered_at) / 3600

            # Drop corrupt rows (same protection as DexertoMerger)
            try:
                entry = json.loads(entry_json)
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"PolymarketMerger: dropping corrupt pending row {entry_id}: {e}")
                with get_db_lock():
                    conn.execute("DELETE FROM polymarket_pending WHERE entry_id = ?", (entry_id,))
                    conn.commit()
                continue

            try:
                # Try one final conversation fetch before posting alone.
                follow_up, tweet2_entry_id = await asyncio.to_thread(
                    self._find_follow_up_sync, entry
                )
                if follow_up:
                    logger.info(
                        f"PolymarketMerger: found follow-up for {entry_id} at flush time — merging"
                    )
                    entry['polymarket_follow_up'] = follow_up
                    if tweet2_entry_id:
                        self._db.mark_processed(tweet2_entry_id)
                else:
                    logger.warning(
                        f"PolymarketMerger: headline {entry_id} waited {age_hours:.1f}h with no "
                        f"follow-up tweet — posting alone"
                    )

                try:
                    success = await self._process_entry(entry)
                except Exception as e:
                    logger.error(
                        f"PolymarketMerger: error flushing stale entry {entry_id}: {e}",
                        exc_info=True
                    )
                    success = False

                if success or self._db.is_processed(entry_id):
                    with get_db_lock():
                        conn.execute("DELETE FROM polymarket_pending WHERE entry_id = ?", (entry_id,))
                        conn.commit()
                else:
                    logger.warning(
                        f"PolymarketMerger: stale flush of {entry_id} did not complete — "
                        f"leaving in pending buffer to retry next cycle"
                    )
            except Exception as e:
                logger.error(
                    f"PolymarketMerger: unexpected error flushing {entry_id}, "
                    f"leaving row for next cycle: {e}",
                    exc_info=True
                )

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    async def _handle_headline_tweet(self, entry: dict) -> bool:
        """Store tweet 1 (headline) in the pending table."""
        entry_id = entry['id']
        conn = get_db_connection()
        with get_db_lock():
            existing = conn.execute(
                "SELECT buffered_at FROM polymarket_pending WHERE entry_id = ?",
                (entry_id,)
            ).fetchone()
            if existing:
                # Already pending — refresh entry_json but keep original buffered_at
                conn.execute(
                    "UPDATE polymarket_pending SET entry_json = ? WHERE entry_id = ?",
                    (json.dumps(entry), entry_id)
                )
                logger.debug(
                    f"PolymarketMerger: refreshed pending headline {entry_id} "
                    f"(buffered {time.time() - existing[0]:.0f}s ago, waiting for follow-up)"
                )
            else:
                conn.execute(
                    "INSERT INTO polymarket_pending (entry_id, entry_json, buffered_at) VALUES (?, ?, ?)",
                    (entry_id, json.dumps(entry), time.time())
                )
                logger.info(f"PolymarketMerger: buffered headline {entry_id} (waiting for follow-up tweet)")
            conn.commit()
        return True  # consumed

    @staticmethod
    def _is_self_contained_headline(entry: dict) -> bool:
        """
        True if this headline tweet already carries a poly.market URL in its
        content — i.e. it is a self-contained post that requires no follow-up.
        These should NOT be buffered; they never get a follow-up reply to wait for.

        Covers two cases:
          1. "🚨 NEW POLYMARKET: ..." posts — these are promotional/standalone
             and already embed the poly.market link in the tweet itself.
          2. Headlines where the odds are embedded in the tweet text (e.g.
             "34% chance." or "2% chance. https://poly.market/...") because
             Polymarket sometimes posts both headline and odds in one tweet.
        """
        content = entry.get('content', '') or entry.get('title', '')
        return bool(POLYMARKET_URL_PATTERN.search(content))

    @staticmethod
    def _status_id_of(entry_id):
        """Extract the integer Twitter status_id from a 'twitter_<id>' entry_id, or None."""
        try:
            return int(entry_id.rsplit('_', 1)[1])
        except (ValueError, IndexError, AttributeError):
            return None

    async def _handle_follow_up_tweet(self, entry: dict) -> bool:
        """Merge tweet 2 (odds + URL) into the headline it replies to."""
        follow_up_entry_id = entry['id']
        follow_up_content = clean_polymarket_follow_up(entry.get('content', '').strip())
        follow_up_sid = self._status_id_of(follow_up_entry_id)

        # If trimming left us with nothing usable (no poly.market URL was present),
        # don't merge card junk into a headline — just discard the follow-up.
        if not follow_up_content:
            logger.info(
                f"PolymarketMerger: follow-up {follow_up_entry_id} had no poly.market URL "
                f"after trimming — marking processed and discarding"
            )
            self._db.mark_processed(follow_up_entry_id)
            return True  # consumed

        conn = get_db_connection()
        with get_db_lock():
            rows = conn.execute(
                "SELECT entry_id, entry_json, buffered_at FROM polymarket_pending"
            ).fetchall()

        if not rows:
            logger.info(
                f"PolymarketMerger: follow-up tweet {follow_up_entry_id} arrived but no "
                f"headline is pending — posting odds alone"
            )
            self._db.mark_processed(follow_up_entry_id)
            await self._process_entry({
                'id': follow_up_entry_id,
                'source': 'polymarket',
                'source_type': 'twitter',
                'title': entry.get('title', ''),
                'content': follow_up_content,
                'link': entry.get('link', ''),
                'status_id': follow_up_sid,
            })
            return True  # consumed

        # Determine the TRUE parent of this follow-up. The status-id heuristic
        # below (closest pending headline with a smaller status_id) is WRONG
        # whenever two headline+reply pairs are interleaved in the feed — it
        # cross-pairs them. The only reliable signal is the follow-up tweet's
        # own reply_id, which gallery-dl returns (this is the inverse of the
        # reply_id check already used in _find_follow_up_sync).
        parent_sid = await asyncio.to_thread(self._fetch_parent_reply_id_sync, entry)
        if parent_sid is not None:
            parent_match = next(
                (r for r in rows if self._status_id_of(r['entry_id']) == parent_sid),
                None,
            )
            if parent_match is not None:
                logger.info(
                    f"PolymarketMerger: follow-up {follow_up_entry_id} reply_id "
                    f"{parent_sid} matches pending headline {parent_match['entry_id']}"
                )
                headline_entry_id, entry_json = parent_match['entry_id'], parent_match['entry_json']
            else:
                # True parent isn't pending (already flushed/posted, or the bot
                # never buffered it). Don't cross-pair it onto a wrong headline —
                # just post the follow-up odds line on its own.
                logger.warning(
                    f"PolymarketMerger: follow-up {follow_up_entry_id} (reply_id "
                    f"{parent_sid}) has no matching pending headline — posting odds alone"
                )
                self._db.mark_processed(follow_up_entry_id)
                await self._process_entry({
                    'id': follow_up_entry_id,
                    'source': 'polymarket',
                    'source_type': 'twitter',
                    'title': entry.get('title', ''),
                    'content': follow_up_content,
                    'link': entry.get('link', ''),
                    'status_id': follow_up_sid,
                })
                return True  # consumed
        else:
            # Couldn't resolve the true parent (gallery-dl failed / rate-limited).
            # Do NOT fall back to the status-id heuristic — that cross-pairs
            # interleaved headline+reply pairs (the bug that glued "19% chance
            # Mark Cuban..." onto the Kenyan goat herder headline). Post the
            # follow-up odds line on its own instead.
            logger.warning(
                f"PolymarketMerger: could not resolve reply_id for {follow_up_entry_id} "
                f"— posting odds alone instead of guessing (status-id heuristic disabled)"
            )
            self._db.mark_processed(follow_up_entry_id)
            await self._process_entry({
                'id': follow_up_entry_id,
                'source': 'polymarket',
                'source_type': 'twitter',
                'title': entry.get('title', ''),
                'content': follow_up_content,
                'link': entry.get('link', ''),
                'status_id': follow_up_sid,
            })
            return True  # consumed

        with get_db_lock():
            conn.execute("DELETE FROM polymarket_pending WHERE entry_id = ?", (headline_entry_id,))
            conn.commit()

        headline_entry = json.loads(entry_json)
        headline_entry['polymarket_follow_up'] = follow_up_content

        self._db.mark_processed(follow_up_entry_id)

        logger.info(
            f"PolymarketMerger: merging {headline_entry_id} + {follow_up_entry_id}\n"
            f"  follow-up: {follow_up_content[:120]}"
        )
        await self._process_entry(headline_entry)
        return True  # consumed

    @staticmethod
    def _fetch_parent_reply_id_sync(entry: dict):
        """
        Fetch the follow-up tweet itself via gallery-dl and return its parent
        status_id (reply_id), i.e. the headline it actually replies to.

        This is the inverse of _find_follow_up_sync: that one walks tweet1's
        conversation looking for a child tweet2; this one takes tweet2 and asks
        Twitter for its parent. The live merge needs the parent to avoid
        cross-pairing interleaved headline+reply pairs.

        Returns int status_id or None on any failure / rate-limit.
        Blocking — call with asyncio.to_thread().
        """
        link = entry.get('link')
        if not link:
            return None
        cmd = [
            sys.executable, '-m', 'gallery_dl',
            '--config', config.GALLERY_DL_CONFIG,
            '--dump-json',
            '--no-download',
            link,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=30,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None
            items = json.loads(result.stdout.strip())
        except Exception as e:
            logger.warning(
                f"PolymarketMerger: parent fetch failed for {entry.get('id')}: {e}"
            )
            return None

        for item in items:
            if not (isinstance(item, list) and len(item) >= 2 and isinstance(item[1], dict)):
                continue
            d = item[1]
            rid = d.get('reply_id')
            if rid:
                try:
                    return int(rid)
                except (ValueError, TypeError):
                    return None
        return None

    # ------------------------------------------------------------------
    # Conversation fetch
    # ------------------------------------------------------------------

    def _find_follow_up_sync(self, entry: dict) -> tuple:
        """
        Fetch the tweet conversation via gallery-dl and look for Polymarket's
        self-reply (tweet 2) containing a poly.market / polymarket.com URL.

        Returns (follow_up_content, tweet2_entry_id) or (None, None).
        Blocking — call with asyncio.to_thread().
        """
        link = entry.get('link')
        if not link:
            logger.debug(f"PolymarketMerger: no link in entry {entry.get('id')} — skipping conversation fetch")
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
                    f"PolymarketMerger: conversation fetch returned nothing for {entry.get('id')} "
                    f"(exit {result.returncode})"
                )
                return None, None
            items = json.loads(result.stdout.strip())
        except Exception as e:
            logger.warning(
                f"PolymarketMerger: conversation fetch failed for {entry.get('id')}: {e}"
            )
            return None, None

        tweet1_id = int(entry['id'].split('_')[1])

        for item in items:
            if not (isinstance(item, list) and len(item) >= 2 and isinstance(item[1], dict)):
                continue
            d = item[1]
            if (
                d.get('reply_id') == tweet1_id
                and d.get('user', {}).get('id') == POLYMARKET_USER_ID
                and ('poly.market' in d.get('content', '') or 'polymarket.com' in d.get('content', ''))
            ):
                follow_up = clean_polymarket_follow_up(d['content'])
                tweet2_id = d.get('tweet_id')
                tweet2_entry_id = f"twitter_{tweet2_id}" if tweet2_id else None
                logger.debug(
                    f"PolymarketMerger: found tweet2 {tweet2_entry_id} in conversation: "
                    f"{follow_up[:80]}"
                )
                return follow_up, tweet2_entry_id

        logger.debug(
            f"PolymarketMerger: conversation fetch got {len(items)} items for "
            f"{entry.get('id')} but none matched tweet2 criteria"
        )
        return None, None

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _ensure_table(self):
        """Create the polymarket_pending table if it doesn't exist."""
        conn = get_db_connection()
        with get_db_lock():
            conn.execute("""
                CREATE TABLE IF NOT EXISTS polymarket_pending (
                    entry_id   TEXT PRIMARY KEY,
                    entry_json TEXT NOT NULL,
                    buffered_at REAL NOT NULL
                )
            """)
            conn.commit()