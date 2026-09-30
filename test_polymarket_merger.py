"""
Regression test for PolymarketMerger follow-up pairing.

The bug: _handle_follow_up_tweet matched a follow-up to the LAST/nearest
pending headline by status_id, which cross-paired interleaved headline+reply
pairs. The fix resolves the follow-up's true parent via gallery-dl reply_id.

This test replays the REAL 2026-08-07 case that produced mis-merges:

  Headline A (Texas "detransition clinic"): sid 2085752764265583032
      -> real reply: Colorado odds 2085756623251722638 (ht4bNhw)
  Headline B (Mount Vernon deputy):          sid 2085756468100309078
      -> real reply: Senate odds  2085752964711350778 (v0nOeCn),
         whose reply_id is 2085750086710026710 (a THIRD headline)

Old heuristic, processing follow-up A while BOTH A and B are pending, would
have glued A's reply onto B (wrong), and B's reply onto... wrong. The new code
must glue A's reply onto A and B's reply onto the correct parent.

We stub _fetch_parent_reply_id_sync + _process_entry to avoid network calls and
DB writes, and assert which headline each follow-up gets merged into.
"""
import asyncio
import json
import types

import config

# Make config importable without side effects if needed; use monkeypatch below.
import polymarket_merger as pm


# --- In-memory stand-ins -------------------------------------------------

class FakeDB:
    def __init__(self):
        self.processed = set()
    def mark_processed(self, entry_id):
        self.processed.add(entry_id)
    def is_processed(self, entry_id):
        return entry_id in self.processed


# Real reply_id values captured from gallery-dl on 2026-08-07 (the live fix
# depends on these being returned for each follow-up).
REAL_PARENTS = {
    "twitter_2085756623251722638": 2085752764265583032,  # Colorado -> Texas
    "twitter_2085752964711350778": 2085750086710026710,  # Senate  -> 3rd headline
}


class Harness:
    """Builds a merger with stubbed side-effects for deterministic testing."""
    def __init__(self, pending_rows):
        self.db = FakeDB()
        self.processed_entries = []  # (entry_id, follow_up_text_or_None)
        self.merger = pm.PolymarketMerger(
            db=self.db,
            process_entry_fn=self._process,
            max_pending_hours=4.0,
        )
        # Stub the network call: return the real reply_id per follow-up.
        self.merger._fetch_parent_reply_id_sync = types.MethodType(
            lambda self, entry: REAL_PARENTS.get(entry['id']), self.merger
        )
        # Seed the pending table in-memory (the merger uses get_db_connection()).
        # We bypass the real DB by stubbing get_db_connection + get_db_lock.
        self._rows = pending_rows  # list of (entry_id, entry_dict, buffered_at)

    async def _process(self, entry):
        # Record the merge: which headline id + attached follow-up text.
        # ALSO: assert every entry carries the full key set main.py's
        # process_entry() requires. The 2026-09-02 bug: the three "posting
        # odds alone" fallback dicts omitted 'source_type', crashing at
        # main.py:203 with KeyError. The stub must dereference it (and its
        # siblings) so a reintroduction FAILS the test instead of passing.
        required = {'id', 'source', 'source_type', 'title', 'content', 'link', 'status_id'}
        missing = required - set(entry.keys())
        assert not missing, f"entry {entry.get('id')} missing required keys: {sorted(missing)}"
        fu = entry.get('polymarket_follow_up')
        self.processed_entries.append((entry['id'], fu))
        return True

    def _install_db_stub(self):
        import polymarket_merger as pm_local
        # Real DB uses sqlite3.Row (key access). Mirror that with dicts.
        rows = [{'entry_id': e, 'entry_json': json.dumps(d), 'buffered_at': b} for (e, d, b) in self._rows]
        class FakeConn:
            def execute(self, *a, **k):
                class C:
                    def fetchall(self_inner): return rows
                    def fetchone(self_inner): return None
                return C()
            def commit(self): pass
        pm_local.get_db_connection = lambda: FakeConn()
        class Lk:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        pm_local.get_db_lock = lambda: Lk()


def make_headline(entry_id, sid, text):
    return entry_id, {
        'id': entry_id, 'source': 'polymarket', 'source_type': 'twitter', 'status_id': sid,
        'title': 'Polymarket', 'content': text, 'link': f'https://x.com/Polymarket/status/{sid}',
    }, 1000.0


def make_followup(entry_id, sid, parent_sid, odds_text, url):
    return {
        'id': entry_id, 'source': 'polymarket', 'status_id': sid,
        'title': 'R to @Polymarket: ',  # triggers follow-up detection
        'content': f'R to @Polymarket: {odds_text}\n{url}',
        'link': f'https://x.com/Polymarket/status/{sid}',
    }


async def run():
    # Pending: Texas headline (A) only. Follow-up Colorado should match A.
    rows = [make_headline('twitter_2085752764265583032', 2085752764265583032,
                          'Texas Children’s Hospital ordered to open the country’s first “detransition clinic”')]
    h = Harness(rows)
    h._install_db_stub()
    fu = make_followup('twitter_2085756623251722638', 2085756623251722638,
                       2085752764265583032, '50% chance Colorado bans underage transgender surgeries.',
                       'https://poly.market/ht4bNhw')
    consumed = await h.merger.handle(fu)
    assert consumed is True, "follow-up should be consumed"
    assert h.processed_entries, "headline should have been processed"
    merged_id, fu_text = h.processed_entries[0]
    assert merged_id == 'twitter_2085752764265583032', f"Colorado odds glued to WRONG headline: {merged_id}"
    assert 'ht4bNhw' in (fu_text or ''), "follow-up text missing ht4bNhw"
    print("[PASS] Colorado odds (ht4bNhw) -> Texas 'detransition clinic' headline")

    # Pending: Mount Vernon headline (B). Senate follow-up's real parent
    # (2085750086710026710) is NOT pending -> should post odds alone, NOT cross-pair onto B.
    rows2 = [make_headline('twitter_2085756468100309078', 2085756468100309078,
                           'Mount Vernon deputy public safety commissioner charged with attempted murder...')]
    h2 = Harness(rows2)
    h2._install_db_stub()
    fu2 = make_followup('twitter_2085752964711350778', 2085752964711350778,
                        2085750086710026710, '69% chance the Senate passes at least $20 billion in supplemental war funding by Sep 30.',
                        'https://poly.market/v0nOeCn')
    consumed2 = await h2.merger.handle(fu2)
    assert consumed2 is True
    # It must NOT have merged onto the Mount Vernon headline.
    merged_ids = [e[0] for e in h2.processed_entries]
    assert 'twitter_2085756468100309078' not in merged_ids, "Senate odds wrongly glued to Mount Vernon headline!"
    assert 'twitter_2085752964711350778' in merged_ids, "Senate odds should post alone"
    print("[PASS] Senate odds (v0nOeCn) -> posted alone (true parent not pending), NOT cross-paired to Mount Vernon")

    # Fallback path: if gallery-dl returns no parent (None), we must NOT guess
    # (status-id heuristic was the source of the cross-pairing bug). Instead the
    # follow-up posts on its own, preserving the odds + URL without gluing them
    # onto the wrong headline.
    h3 = Harness(rows)
    h3._install_db_stub()
    h3.merger._fetch_parent_reply_id_sync = types.MethodType(lambda self, e: None, h3.merger)
    fu3 = make_followup('twitter_2085756623251722638', 2085756623251722638,
                        2085752764265583032, '50% chance Colorado bans underage transgender surgeries.',
                        'https://poly.market/ht4bNhw')
    await h3.merger.handle(fu3)
    # Follow-up must post alone — NOT glued to the pending headline.
    merged_ids = [e[0] for e in h3.processed_entries]
    assert 'twitter_2085752764265583032' not in merged_ids, \
        "Fallback must NOT glue follow-up to pending headline (cross-pairing bug)"
    assert 'twitter_2085756623251722638' in merged_ids, \
        "Fallback should post the follow-up alone"
    print("[PASS] Fallback (no reply_id) posts follow-up alone — no cross-pairing")


if __name__ == '__main__':
    asyncio.run(run())
    print("\nALL POLYMARKET MERGER TESTS PASSED")
