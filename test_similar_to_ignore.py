"""
Regression test: LLM-confirmed near-duplicate entries must post to the
Discord ignore channel instead of being silently hard-suppressed.

The bug (2026-09-07): a Telegram message (telegram_news_crypto_24736,
01:47 a.m.) was LLM-confirmed as a near-duplicate (0.784) and the pipeline
logged "Similar story suppressed (not posted)" then returned early --
marked processed + embedded, but NEVER posted anywhere. No message_mapping
row, no Discord message, not even in the ignore channel. The config comment
for SIMILARITY_THRESHOLD says "Similar content - route to ignore channel",
and the exact-duplicate path already does that (override category to
'ignore', fall through to the normal post). The near-duplicate path
instead hard-suppressed. The matched entry was a "ghost" (an entry that
itself never posted because it was suppressed against an earlier one), so
the real story died three times in a row.

The contract Brandi stated: every entry -- including confirmed
near-duplicates -- must end up visible in the Discord ignore channel.
Nothing may be silently suppressed with no Discord trace.

This test replays the real-world condition: a new Telegram entry matches a
ghost anchor (find_best_match returns a match entry_id that has NO
message_mapping row, so get_discord_message_info returns None). It stubs
every network/DB component and asserts post_message() is called exactly
once with category='ignore'.
"""
import asyncio
import logging
import types
import unittest

# Never let the test write into the live bot.log (importing main pulls in
# the real utils logger chain).
logging.disable(logging.CRITICAL)

import config
import main


GHOST_PREVIEW = "4,000 Bitcoin worth $320 million withdrawn following Liquid Network hack."
GHOST_CONTENT = ("4,000 Bitcoin worth $320 million withdrawn following Liquid Network hack.\n"
                 "The hacker is now communicating with the exchange about returning the funds.")
IGNORE_CHANNEL = 1344410355224547441

ENTRY = {
    'id': 'telegram_news_crypto_TEST_FIX',
    'source': 'news_crypto',
    'source_type': 'telegram',
    'message_id': 999999,
    'title': 'Liquid Network halts transactions',
    'link': 'https://t.me/news_crypto/999999',
    'content': ('BREAKING: Liquid Network has halted all transactions after a security '
                'exploit reportedly drained around $320 million worth of Bitcoin.'),
}


class FakeDB:
    """Stub of the Database class: only the methods process_entry touches."""

    def __init__(self):
        self._embeddings_cache = {}
        self.marked = []
        self.embedded = []
        self.mappings = []
        self.is_processed_calls = 0

    def is_processed(self, entry_id):
        self.is_processed_calls += 1
        return False

    def find_best_match(self, embedding):
        # Ghost anchor: the matched entry has an embedding but NO mapping row.
        return (0.784, GHOST_PREVIEW, GHOST_CONTENT, 'twitter_GHOST')

    def find_top_matches(self, embedding, threshold=0.0, limit=3):
        return [(0.784, GHOST_PREVIEW, GHOST_CONTENT, 'twitter_GHOST')]

    def get_discord_message_info(self, entry_id):
        # Ghost anchor was never posted -> no mapping -> None.
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
        return True  # LLM says SAME story

    def categorize(self, content, *args):
        return ('crypto', 'test reasoning', None)


class FakeMediaHandler:
    async def download_telegram_media(self, entry):
        return entry  # text-only message, no media

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


def make_bot():
    bot = main.NewsAggregatorBot.__new__(main.NewsAggregatorBot)
    bot.db = FakeDB()
    bot.removed_entries_db = types.SimpleNamespace(is_removed=lambda eid: False)
    bot.ollama = FakeOllama()
    bot.media_handler = FakeMediaHandler()
    bot.discord_poster = FakePoster()
    bot.telegram_poller = FakeTelegramPoller()
    bot.retry_queue = types.SimpleNamespace()
    bot.stats = {'processed': 0, 'duplicates': 0, 'errors': 0, 'by_category': {}}
    bot._processing_lock = set()
    bot._inflight_embeddings = {}
    bot._recent_post_times = __import__('collections').deque()
    return bot


def run_case():
    bot = make_bot()
    result = asyncio.run(bot.process_entry(dict(ENTRY)))

    # Contract: the near-duplicate MUST have been posted to the ignore channel.
    assert result is True, f"process_entry returned {result!r}, expected True"
    assert len(bot.discord_poster.post_calls) == 1, (
        f"post_message called {len(bot.discord_poster.post_calls)} times, expected exactly 1 "
        f"(near-duplicate must be routed to ignore, not hard-suppressed)")
    post = bot.discord_poster.post_calls[0]
    assert post['category'] == 'ignore', (
        f"posted as {post['category']!r}, expected 'ignore'")
    assert 'Liquid Network has halted' in post['content'], (
        f"posted content lost the story: {post['content']!r}")
    assert post['source_type'] == 'telegram', post.get('source_type')

    # The normal post-path bookkeeping must have run (mark + embedding + mapping).
    assert bot.db.marked == ['telegram_news_crypto_TEST_FIX'], bot.db.marked
    assert any(eid == 'telegram_news_crypto_TEST_FIX' for eid, _ in bot.db.embedded), bot.db.embedded
    assert len(bot.db.mappings) == 1, f"expected 1 message_mapping row, got {len(bot.db.mappings)}"
    mapping = bot.db.mappings[0]
    assert mapping.get('telegram_entry_id') == 'telegram_news_crypto_TEST_FIX', mapping
    assert mapping['discord_message_id'] == 777777, mapping['discord_message_id']
    # ... and the Telegram poller offset must advance so the entry isn't re-fetched forever.
    assert bot.telegram_poller.offset_updates == [('telegram_news_crypto_TEST_FIX', 999999)], \
        bot.telegram_poller.offset_updates

    print("[PASS] Near-duplicate posted to ignore channel with full bookkeeping")
    print("[PASS] placement_reason in mapping:", mapping.get('placement_reason'))


if __name__ == '__main__':
    run_case()
    print("\nALL SIMILAR-TO-IGNORE TESTS PASSED")
