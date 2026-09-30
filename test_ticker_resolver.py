"""Plain-python tests for ticker resolution (no pytest in venv).

Run: cd "/home/brandi/newsbot strikes back" && .venv/bin/python test_ticker_resolver.py
Exit 0 = all pass. Network is stubbed, so this is offline-safe.
"""
import os
import tempfile

import ticker_resolver as tr
from utils import normalize_crypto_tickers

_fail = 0


def check(name, got, want):
    global _fail
    ok = got == want
    print(("PASS" if ok else "FAIL"), name, "->", repr(got))
    if not ok:
        print("   expected:", repr(want))
        _fail += 1


def fresh_cache():
    """Reset the in-process cache to a throwaway file."""
    tr._cache = {}
    tr._cache_path = lambda: os.path.join(tempfile.mkdtemp(), "c.json")


# ------------------------------------------------------------------ static pass
# Natives resolve from the static dict with NO network call.
_real_query = tr._query_dexscreener
tr._query_dexscreener = lambda c, a: (_ for _ in ()).throw(AssertionError("static pass should not hit network"))
check("static BTC", normalize_crypto_tickers("bitcoin:native to $65k"), "$BTC to $65k")
check("static SOL", normalize_crypto_tickers(
    "solana:So11111111111111111111111111111111111111112 up"), "$SOL up")
tr._query_dexscreener = _real_query

# ------------------------------------------------------------------ dynamic pass
fresh_cache()
tr._query_dexscreener = lambda chain, addr: {
    "0x39dbed3a2bd333467115de45665cc57f813c4571": ("PONS", "Pons"),
    "0x020bfc650a365f8bb26819deaabf3e21291018b4": ("CASHCAT", "Cash Cat"),
}.get(addr.lower(), (None, None))

check("robinhood -> PONS",
      normalize_crypto_tickers("robinhood:0x39dbed3a2bd333467115de45665cc57f813c4571 rocks"),
      "$PONS rocks")
check("two codes in one line",
      normalize_crypto_tickers(
          "robinhood:0x39dbed3a2bd333467115de45665cc57f813c4571 robinhood:0x020bfc650a365f8bb26819deaabf3e21291018b4"),
      "$PONS $CASHCAT")
check("uppercases mixed-case symbol",
      normalize_crypto_tickers("robinhood:0x020bfc650a365f8bb26819deaabf3e21291018b4"),
      "$CASHCAT")

# ------------------------------------------------------------------ cache behaviour
fresh_cache()
calls = {"n": 0}


def _count(chain, addr):
    calls["n"] += 1
    return ("CASHCAT", "Cash Cat")


tr._query_dexscreener = _count
normalize_crypto_tickers("robinhood:0x020bfc650a365f8bb26819deaabf3e21291018b4")
normalize_crypto_tickers("robinhood:0x020bfc650a365f8bb26819deaabf3e21291018b4")
check("cache: one network fetch for two passes", calls["n"], 1)

# ------------------------------------------------------------------ guards
# "query:" is not a trusted chain prefix -> untouched, no network.
fresh_cache()
tr._query_dexscreener = lambda c, a: (_ for _ in ()).throw(AssertionError("must not query"))
check("query: false positive untouched",
      normalize_crypto_tickers("query:TTiDLWE6fZK8okMJv6ijg42yrH6W2pjSr9"),
      "query:TTiDLWE6fZK8okMJv6ijg42yrH6W2pjSr9")

# Unresolved code is LEFT UNCHANGED (Brandi's call).
fresh_cache()
tr._query_dexscreener = lambda c, a: (None, None)
check("unresolved kept as raw code",
      normalize_crypto_tickers("robinhood:0x000000000000000000000000000000000000dead"),
      "robinhood:0x000000000000000000000000000000000000dead")

# ------------------------------------------------------------------ caps guard
check("caps: lowercase ticker uppercased", tr.keep_tickers_all_caps("$pons up"), "$PONS up")
check("caps: mixed-case uppercased", tr.keep_tickers_all_caps("$JitoSOL"), "$JITOSOL")
check("caps: money amounts untouched", tr.keep_tickers_all_caps("costs $100 and $1.5M"), "costs $100 and $1.5M")
check("caps: existing caps unchanged", tr.keep_tickers_all_caps("$PONS $CASHCAT"), "$PONS $CASHCAT")

print("\nFAILURES:", _fail)
raise SystemExit(1 if _fail else 0)
