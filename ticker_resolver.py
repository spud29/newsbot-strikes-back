"""Resolve X "chain:address" ticker codes to their human-readable $TICKER.

X renders `robinhood:0x39dbed...` as `$PONS` client-side; the display text is
never sent to us (neither Nitter RSS nor gallery-dl carries it). We reconstruct
it by resolving the contract/mint address to its symbol via Dexscreener, which
supports robinhood, solana, ethereum, base, and more. Results are cached in
data/ticker_symbols.json so each address hits the network once.

Failures degrade to None: the caller leaves the raw code unchanged (Brandi's
call — better an honest hex stamp than a wrong/blank ticker).
"""
import json
import os
import re
import threading
import time

import requests

import config
from utils import logger

# ---- regexes -----------------------------------------------------------------
# EVM: 0x + 40 hex (robinhood, ethereum, base, ...).
# Solana: base58, 32-44 chars (base58 excludes 0 O I l).
_EVM = re.compile(r'^0x[0-9a-fA-F]{40}$')
_BASE58 = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')

# Any "<prefix>:<address>" occurrence in text. Lookarounds stop the match from
# bleeding into a longer token (e.g. a URL path).
_CODE_RE = re.compile(
    r'(?<![A-Za-z0-9_:])'
    r'(?P<chain>[a-z][a-z0-9_]{1,15})'
    r':(?P<addr>0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})'
    r'(?![A-Za-z0-9_])'
)

# Cashtag token: $ + letter + 1-12 alnum. Money ($100, $1.5M) never matches.
_TICKER_TOKEN_RE = re.compile(r'\$[A-Za-z][A-Za-z0-9]{1,12}\b')

# In-process cache. Value per key: {"symbol": str|None, "name": str|None, "ts": float}
_cache_lock = threading.Lock()
_cache = None


def _cache_path():
    return getattr(
        config,
        "TICKER_RESOLVE_CACHE_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ticker_symbols.json"),
    )


def _load_cache():
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(_cache_path(), "r", encoding="utf-8") as fh:
            _cache = json.load(fh)
    except (FileNotFoundError, ValueError):
        _cache = {}
    return _cache


def _save_cache():
    if _cache is None:
        return
    path = _cache_path()
    tmp = f"{path}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_cache, fh)
        os.replace(tmp, path)  # atomic
    except OSError as e:
        logger.warning(f"TickerResolver: cache write failed: {e}")


def _query_dexscreener(chain, address):
    """Return (symbol, name) from Dexscreener, or (None, None) on miss/error."""
    chain_id = (getattr(config, "TICKER_CHAIN_ID_MAP", {}) or {}).get(chain, chain)
    url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
    try:
        r = requests.get(
            url,
            timeout=getattr(config, "TICKER_RESOLVE_TIMEOUT", 5),
            headers={"User-Agent": "newsbot/1.0"},
        )
        r.raise_for_status()
        pairs = (r.json() or {}).get("pairs") or []
    except Exception as e:
        logger.warning(f"TickerResolver: lookup failed for {chain}:{address}: {e}")
        return None, None

    addr_l = address.lower()
    best = None  # (liquidity, symbol, name)
    for p in pairs:
        base = p.get("baseToken") or {}
        quote = p.get("quoteToken") or {}
        # Only trust pairs on the expected chain.
        if chain_id and p.get("chainId") not in (None, chain_id):
            continue
        if (base.get("address", "") or "").lower() == addr_l:
            tok = base
        elif (quote.get("address", "") or "").lower() == addr_l:
            tok = quote
        else:
            continue
        liq = ((p.get("liquidity") or {}).get("usd") or 0)
        if best is None or liq > best[0]:
            best = (liq, tok.get("symbol"), tok.get("name"))
    if best is None:
        return None, None
    sym = (best[1] or "").strip()
    return (sym or None), (best[2] or None)


def resolve_chain_ticker(chain, address):
    """Resolve one code to its ticker symbol (UPPERCASE), using/updating cache."""
    key = f"{chain.lower()}:{address.lower()}"
    now = time.time()
    with _cache_lock:
        cache = _load_cache()
        hit = cache.get(key)
        if hit:
            if hit.get("symbol"):
                return hit["symbol"].upper()
            # negative hit still fresh -> don't retry yet
            if now - hit.get("ts", 0) < getattr(config, "TICKER_RESOLVE_MISS_TTL", 86400):
                return None

    if not getattr(config, "TICKER_RESOLVE_ENABLED", True):
        return None

    symbol, name = _query_dexscreener(chain, address)
    if symbol:
        symbol = symbol.upper()  # tickers stay ALL CAPS (Brandi's call)
    with _cache_lock:
        cache = _load_cache()
        cache[key] = {"symbol": symbol, "name": name, "ts": now}
        _save_cache()
    if symbol:
        logger.info(f"TickerResolver: {chain}:{address[:12]}... -> ${symbol}")
    else:
        logger.debug(f"TickerResolver: unresolved {chain}:{address[:12]}...")
    return symbol


def resolve_codes_in_text(text):
    """Rewrite every trusted "<chain>:<address>" code to $TICKER. Unknown codes
    are left unchanged. Never raises."""
    if not text:
        return text
    prefixes = getattr(config, "TICKER_CHAIN_PREFIXES", set()) or set()

    def _sub(m):
        chain = m.group("chain").lower()
        addr = m.group("addr")
        if chain not in prefixes:
            return m.group(0)                       # e.g. "query:..." -> untouched
        if not (_EVM.match(addr) or _BASE58.match(addr)):
            return m.group(0)
        sym = resolve_chain_ticker(chain, addr)
        return f"${sym}" if sym else m.group(0)     # unresolved -> keep raw code

    try:
        return _CODE_RE.sub(_sub, text)
    except Exception as e:
        logger.warning(f"TickerResolver: text pass failed: {e}")
        return text


def keep_tickers_all_caps(text):
    """Force cashtag tickers ($pons -> $PONS) to ALL CAPS.

    X renders tickers in the token's own case; we normalize to caps so they also
    survive the bot's ALL-CAPS text cleanup step. Only $ + letter + 1-12 alnum
    matches, so money amounts ($100, $1.5M) are untouched. Never raises.
    """
    if not text:
        return text
    try:
        return _TICKER_TOKEN_RE.sub(lambda m: m.group(0).upper(), text)
    except Exception:
        return text
