"""
Entry enrichment for the Newsbot.

Adds a "[Context]" block to entries before categorization/newsworthiness scoring
so borderline-vague stories get judged with full information:

  - Entry has an article URL  -> Firecrawl scrape (no Tavily credits used)
  - Bare vague headline       -> Tavily search   (1 credit per search)

Cost guards:
  - TAVILY_DAILY_CAP limits searches per calendar day (default 25).
  - Enrichment is skipped entirely when disabled or no keys are present.
  - All network failures degrade gracefully to "unenriched" — enrichment must
    never break posting.

The enriched text is stored in entry['enrichment_context']; main.py appends it
to content before categorization. The original tweet text is never modified.
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request

from utils import logger


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

UTM_PARAMS = re.compile(
    r'[?&](utm_[a-z]+|fbclid|gclid|ref_src|ref_url|snd|cmpid)=[^&#]*', re.I)


def clean_url(url: str) -> str:
    """Strip tracking params (utm_*, fbclid, etc.) from a URL."""
    return UTM_PARAMS.sub('', url).rstrip('?&')


def first_article_url(text: str) -> str | None:
    """Extract the first real article URL from entry text.

    Skips t.co wrappers, x.com/twitter links, and poly.market links — those
    aren't article pages worth scraping.
    """
    if not text:
        return None
    urls = re.findall(r'https?://[^\s>)]+', text)
    skip = ('t.co/', 'x.com/', 'twitter.com/', 'poly.market',
            'polymarket.com', 'video.twimg.com')
    for url in urls:
        url = url.rstrip('.,;:')
        if not any(s in url for s in skip):
            return clean_url(url)
    return None


# ---------------------------------------------------------------------------
# Daily credit cap (persisted so restarts don't reset it)
# ---------------------------------------------------------------------------

class DailyCap:
    """Track Tavily usage against a per-calendar-day cap, persisted on disk."""

    def __init__(self, path: str, daily_cap: int):
        self.path = path
        self.cap = max(1, int(daily_cap))
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(self.path):
            with open(self.path, 'w') as f:
                json.dump({'day': '', 'count': 0}, f)

    def _load(self) -> dict:
        try:
            with open(self.path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {'day': '', 'count': 0}

    def _save(self, day: str, count: int):
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'day': day, 'count': count}, f)
        os.replace(tmp, self.path)

    def try_consume(self) -> bool:
        """Return True if a search is allowed under today's cap."""
        today = time.strftime('%Y-%m-%d')
        state = self._load()
        if state.get('day') != today:
            state = {'day': today, 'count': 0}
        if state['count'] >= self.cap:
            logger.warning(
                f"Enrichment: Tavily daily cap ({self.cap}) reached — "
                f"skipping search for today")
            return False
        state['count'] += 1
        self._save(state['day'], state['count'])
        return True


# ---------------------------------------------------------------------------
# Firecrawl extraction (free path)
# ---------------------------------------------------------------------------

# Site-chrome phrases to drop from scraped markdown before using it as context
_CHROME_PATTERNS = (
    'weather alert', 'breaking news', 'show ', 'close', 'sign in', 'subscribe',
    'advertis', 'share this', 'published:', 'updated:', 'skip to',
    'newsletter', 'cookie', 'privacy policy', 'terms of', 'all rights',
    'in effect for',
)


def clean_extract(md: str) -> str:
    """Turn scraped markdown into plain prose (no chrome, no markup)."""
    md = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', md)          # drop images
    md = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', md)      # links -> text
    md = re.sub(r'^#{1,6}\s*', '', md, flags=re.M)        # headers
    md = re.sub(r'#{1,6}', '', md)                        # inline header marks
    md = md.replace('[...]', '…').replace('[... ]', '…')  # truncation markers
    md = re.sub(r'[*_]{1,3}', '', md)                     # emphasis
    lines = []
    for raw in md.split('\n'):
        line = raw.strip()
        if not line or len(line) < 30:
            continue
        low = line.lower()
        if any(p in low for p in _CHROME_PATTERNS):
            continue
        lines.append(line)
    return ' '.join(lines)


def _domain(url: str) -> str:
    """Bare domain from a URL, e.g. 'bloomberg.com'."""
    m = re.match(r'https?://(?:www\.)?([^/]+)', url or '')
    return m.group(1) if m else ''


def first_sentences(text: str, max_chars: int = 240) -> str:
    """First 1-2 real sentences of prose, trimmed to max_chars.

    Returns '' when the text doesn't contain enough real prose (nav junk,
    dashboards, ticker spam) so callers can skip enrichment entirely.
    """
    if not text:
        return ''
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    out = []
    for p in parts:
        p = p.strip()
        if len(p) < 45 or '|' in p or not re.search(r'[a-z]{3}', p):
            continue
        letters = sum(c.isalpha() for c in p)
        if letters / max(len(p), 1) < 0.55:   # mostly digits/symbols -> junk
            continue
        spaces = p.count(' ')
        if spaces == 0 or letters / max(spaces, 1) > 12:  # no/concatenated words -> nav junk
            continue
        # Real prose: avg word length 3–9 chars, no word longer than ~18,
        # and not dominated by Title Case nav labels.
        words = p.split()
        avg_w = sum(len(w) for w in words) / max(len(words), 1)
        if avg_w > 11 or max(len(w) for w in words) > 22:
            continue
        # Real prose: ~10-25% capitalized words. Dashboards/nav: 50-80%.
        cap_ratio = sum(1 for w in words if w[0].isupper()) / max(len(words), 1)
        if cap_ratio > 0.4:
            continue
        # Found a real sentence — trim and return it
        result = p[:max_chars].rsplit(' ', 1)[0].rstrip(',;:') + '…' if len(p) > max_chars else p
        return result
    return ''


def firecrawl_scrape(url: str, api_key: str,
                     timeout: float = 20.0) -> str | None:
    """Scrape an article URL via Firecrawl; return cleaned prose or None."""
    body = json.dumps({
        'url': url,
        'formats': ['markdown'],
        'onlyMainContent': True,
    }).encode()
    req = urllib.request.Request(
        'https://api.firecrawl.dev/v1/scrape',
        data=body,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        if not data.get('success'):
            return None
        md = (data.get('data') or {}).get('markdown') or ''
        if len(md.strip()) < 80:
            return None
        # Clean site chrome and keep the article lede
        return clean_extract(md)
    except Exception as e:
        logger.debug(f"Enrichment: Firecrawl scrape failed for {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Tavily search (credit-using path)
# ---------------------------------------------------------------------------

def tavily_search(query: str, api_key: str,
                  timeout: float = 15.0) -> list[dict]:
    """Run a Tavily basic search; return up to 2 results as dicts."""
    body = json.dumps({
        'api_key': api_key,
        'query': query,
        'search_depth': 'basic',
        'max_results': 2,
        'include_answer': False,
    }).encode()
    req = urllib.request.Request(
        'https://api.tavily.com/search',
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        results = []
        for r in (data.get('results') or [])[:2]:
            # Clean site chrome/nav junk out of the raw snippet
            snippet = clean_extract(r.get('content') or '')
            if len(snippet) < 40:
                snippet = (r.get('content') or '')[:300].strip()
            results.append({
                'title': (r.get('title') or '').strip(),
                'snippet': snippet,
                'url': r.get('url') or '',
            })
        return results
    except Exception as e:
        logger.debug(f"Enrichment: Tavily search failed for {query!r}: {e}")
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def should_enrich(content: str) -> tuple[bool, str]:
    """Decide whether/how to enrich an entry.

    Returns (should_enrich, mode) where mode is 'firecrawl' | 'tavily' | ''.
    Rules:
      - Has a usable article URL          -> firecrawl (always attempted;
                                             costs no Tavily credits)
      - Bare headline, short, no URL      -> tavily
      - Long/self-explanatory, no URL     -> skip
      - Already enriched                  -> skip
    """
    if '[Context]' in content:
        return False, ''
    url = first_article_url(content)
    if url:
        return True, 'firecrawl'
    stripped = content.strip()
    if len(stripped) <= 300:
        return True, 'tavily'
    return False, ''


def enrich_entry(entry_id: str, content: str,
                 firecrawl_key: str | None, tavily_key: str | None,
                 cap: DailyCap) -> str | None:
    """Produce the [Context] block for an entry, or None.

    Never raises — all failures return None so the pipeline continues.
    """
    go, mode = should_enrich(content)
    if not go:
        return None

    context_text = None
    if mode == 'firecrawl':
        if not firecrawl_key:
            return None
        url = first_article_url(content)
        extract = firecrawl_scrape(url, firecrawl_key)
        lede = first_sentences(extract) if extract else ''
        if lede:
            domain = _domain(url)
            context_text = (
                f"> **Context** ({domain}):\n"
                f"> {lede}"
            )
            logger.info(
                f"Enrichment: Firecrawl context added for {entry_id} "
                f"({len(lede)} chars)")
    elif mode == 'tavily':
        if not tavily_key:
            return None
        if not cap.try_consume():
            return None
        # Query = the headline itself, trimmed of newlines/quotes noise
        query = re.sub(r'\s+', ' ', content.strip())[:180]
        results = tavily_search(query, tavily_key)
        if results:
            lines = []
            for r in results:
                title = r['title']
                snippet = first_sentences(r['snippet'], max_chars=200)
                src = _domain(r['url'])
                entry_line = f"> **{title}**" + (f" — {src}" if src else "")
                if snippet:
                    entry_line += f"\n> {snippet}"
                lines.append(entry_line)
            context_text = "\n".join(lines)
            logger.info(
                f"Enrichment: Tavily context added for {entry_id} "
                f"({len(results)} results)")

    if not context_text:
        return None
    return f"\n\n**[Context]**\n{context_text}"
