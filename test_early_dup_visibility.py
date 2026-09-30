"""
Regression test: same-cycle content-hash duplicates and concurrent-sibling
near-duplicates must be posted to the Discord ignore channel instead of
being silently suppressed.

The bug (found 2026-09-23 while reviewing the deep-dive improvement plan):
9 entries since the 2026-09-07 near-duplicate fix were still vanishing with
zero Discord trace. Log cross-reference showed they came from the two EARLY
suppression paths the 9/7 fix did not cover:

  - "Same-cycle content-hash duplicate: <id> matches <other> — suppressing"
    (main.py, content-hash check against the DB embeddings cache)
  - "Concurrent-sibling duplicate: <id> matches an entry still being
    processed this cycle — suppressing to avoid a double-post"
    (main.py, in-flight embedding registry check)

Both branches did mark_processed + add_embedding + cleanup + return True:
processed and embedded, but NO post, NO message_mapping row — a ghost.
Brandi's standing contract: every entry must be visible in the ignore
channel, including duplicates. The fix routes both paths through the
existing duplicate_info ignore-routing machinery (same pattern as the 9/7
near-duplicate fix).

These tests stub every network/DB component and assert post_message() is
called exactly once with category='ignore' and full bookkeeping.
"""
import asyncio
import logging
import types
import unittest
from collections import deque

import numpy as np

# Never let the test write into the live bot.log (importing main pulls in
# the real utils logger chain).
logging.disable(logging.CRITICAL)

import config
import main


IGNORE_CHANNEL = 1344410355224547441

DUPE_TEXT = ("BREAKING: Liquid Network has halted all transactions after a security "
             "exploit reportedly drained around $320 million worth of Bitcoin.")

ENTRY = {
    'id': 'twitter_EARLYDUP_TEST',
    'source': 'unusual_whales',
    'source_type': 'twitter',
    'title': 'Liquid Network halts transactions',
    'link': 'https://x.com/unusual_whales/status/999999',
    'content': DUPE_TEXT,
}


class FakeDB:
    """Stub of the Database class: only the methods process_entry touches."""

    def __init__(self, cache=None):
        self._embeddings_cache = cache or {}
        self.marked = []
        self.embedded = []
        self.mappings = []
        self.is_processed_calls = 0

    def is_processed(self, entry_id):
        self.is_processed_calls += 1
        return False

    def find_best_match(self, embedding):
        return (0.0, None, None, None)

    def find_top_matches(self, embedding, threshold=0.0, limit=3):
        return []

    def get_discord_message_info(self, entry_id):
        return None

    def mark_processed(self, entry_id):
        self.marked.append(entry_id)

    def add_embedding(self, content, embedding, entry_id=None):
        self.embedded.append((entry_id, content[:40]))

    def store_message_mapping(self, **kwargs):
        self.mappings.append(kwargs)


class FakeOllama:
    def generate_embedding(self, content):
        return [0.1] * 8

    def verify_similarity(self, content, cand_content):
        return True

    def categorize(self, content, *args):
        return ('crypto', 'test reasoning', None)


class FakeMediaHandler:
    def download_twitter_media(self, entry):
        return entry  # text-only, no media (called via asyncio.to_thread — must be sync)

    def cleanup_entry_media(self, entry):
        pass


class FakePoster:
    def __init__(self):
        self.post_calls = []

    async def post_message(self, **kwargs):
        self.post_calls.append(kwargs)
        return (True, 777777, IGNORE_CHANNEL)


class FakeTelegramPoller:
    def __init__(self):
        self.offset_updates = []

    def update_last_message_id(self, entry_id, message_id):
        self.offset_updates.append((entry_id, message_id))


def make_bot(db):
    bot = main.NewsAggregatorBot.__new__(main.NewsAggregatorBot)
    bot.db = db
    bot.removed_entries_db = types.SimpleNamespace(is_removed=lambda eid: False)
    bot.ollama = FakeOllama()
    bot.media_handler = FakeMediaHandler()
    bot.discord_poster = FakePoster()
    bot.telegram_poller = FakeTelegramPoller()
    bot.retry_queue = types.SimpleNamespace()
    bot.stats = {'processed': 0, 'duplicates': 0, 'errors': 0, 'by_category': {}}
    bot._processing_lock = set()
    bot._inflight_embeddings = {}
    bot._recent_post_times = deque()
    return bot


def assert_routed_to_ignore(bot, db, expect_source, case_name):
    """Shared contract assertions for both early-duplicate paths."""
    result_len = len(bot.discord_poster.post_calls)
    assert result_len == 1, (
        f"[{case_name}] post_message called {result_len} times, expected exactly 1 "
        f"(early duplicates must be routed to ignore, not silently suppressed)")
    post = bot.discord_poster.post_calls[0]
    assert post['category'] == 'ignore', (
        f"[{case_name}] posted as {post['category']!r}, expected 'ignore'")
    assert 'liquid network' in post['content'].lower(), (
        f"[{case_name}] posted content lost the story: {post['content']!r}")
    assert post['source_type'] == 'twitter', post.get('source_type')

    # Full post-path bookkeeping: mark + embedding + mapping row.
    assert db.marked == ['twitter_EARLYDUP_TEST'], (
        f"[{case_name}] mark_processed not called by post path: {db.marked}")
    assert any(eid == 'twitter_EARLYDUP_TEST' for eid, _ in db.embedded), (
        f"[{case_name}] embedding not stored: {db.embedded}")
    assert len(db.mappings) == 1, (
        f"[{case_name}] expected 1 message_mapping row, got {len(db.mappings)}")
    mapping = db.mappings[0]
    placement = mapping.get('placement_reason') or ''
    assert 'Duplicate override' in placement, (
        f"[{case_name}] placement_reason missing 'Duplicate override': {placement!r}")
    assert f"via {expect_source}" in placement, (
        f"[{case_name}] placement_reason missing source tag '{expect_source}': {placement!r}")
    print(f"[PASS] {case_name}: posted to ignore, mapping row + placement_reason correct")
    return mapping


def run_same_cycle_hash_case():
    """Same-cycle content-hash duplicate -> ignore channel, not a ghost."""
    cache = {
        'abc123': {
            'entry_id': 'twitter_ORIGINAL',
            'content': DUPE_TEXT,  # identical text -> md5 match
        }
    }
    bot = make_bot(FakeDB(cache))
    result = asyncio.run(bot.process_entry(dict(ENTRY)))
    assert result is True, f"process_entry returned {result!r}, expected True"
    assert_routed_to_ignore(bot, bot.db, 'same-cycle content-hash duplicate',
                            'same-cycle content-hash duplicate')


def run_concurrent_sibling_case():
    """Concurrent-sibling near-duplicate -> ignore channel, not a ghost."""
    bot = make_bot(FakeDB())  # empty cache; no DB match at all
    # Pre-register a sibling still mid-pipeline with an identical embedding.
    emb = np.array([0.1] * 8)
    bot._inflight_embeddings['twitter_SIBLING'] = (emb, float(np.linalg.norm(emb)))
    result = asyncio.run(bot.process_entry(dict(ENTRY)))
    assert result is True, f"process_entry returned {result!r}, expected True"
    assert_routed_to_ignore(bot, bot.db, 'concurrent-sibling duplicate',
                            'concurrent-sibling duplicate')


if __name__ == '__main__':
    run_same_cycle_hash_case()
    run_concurrent_sibling_case()
    print("\nALL EARLY-DUP-VISIBILITY TESTS PASSED")
