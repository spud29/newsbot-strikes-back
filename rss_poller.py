"""
RSS feed poller for Twitter feeds
"""
import feedparser
import re
import requests
import time
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils import logger, retry_with_backoff, extract_urls_from_html, clean_text_content, remove_twitter_attribution
import config

class RSSPoller:
    """Polls RSS feeds for new Twitter entries"""
    
    def __init__(self):
        """Initialize RSS poller"""
        self.feeds = config.RSS_FEEDS
        self._feed_429_strikes = {}      # feed_name -> consecutive failed-429 cycles
        self._feed_cooldown_until = {}   # feed_name -> epoch seconds
        self._429_COOLDOWN_SECONDS = 1800      # 30 min
        self._429_STRIKE_LIMIT = 3             # consecutive failed cycles before cooldown
        logger.info(f"RSS Poller initialized with {len(self.feeds)} feeds")

    def poll_feed(self, feed_name, feed_url):
        """
        Poll a single RSS feed, with a feed-level 429 cooldown.

        Wrapper that owns the strike/cooldown bookkeeping so a cooldown is keyed
        to whole cycles, not retry attempts: only a cycle where ALL retry
        attempts 429 counts as one strike (transient 429s that succeed on retry
        reset the counter, so they never trigger a cooldown).

        Args:
            feed_name: Name of the feed
            feed_url: URL of the RSS feed

        Returns:
            list: List of entry dictionaries (empty if in cooldown)
        """
        cooldown_until = self._feed_cooldown_until.get(feed_name, 0)
        if time.time() < cooldown_until:
            logger.debug(f"Feed {feed_name} in 429 cooldown, skipping")
            return []
        if cooldown_until and time.time() >= cooldown_until:
            # Cooldown expired — give the feed a fresh chance.
            self._feed_429_strikes[feed_name] = 0

        try:
            entries = self._poll_feed_with_retry(feed_name, feed_url)
            # Success (even after retries) means the feed is fine.
            self._feed_429_strikes[feed_name] = 0
            return entries
        except Exception as e:
            is_429 = (
                "429" in str(e)
                or (getattr(e, "response", None) is not None
                    and getattr(e.response, "status_code", None) == 429)
            )
            if is_429:
                strikes = self._feed_429_strikes.get(feed_name, 0) + 1
                self._feed_429_strikes[feed_name] = strikes
                if strikes >= self._429_STRIKE_LIMIT:
                    self._feed_cooldown_until[feed_name] = time.time() + self._429_COOLDOWN_SECONDS
                    logger.warning(
                        f"Feed {feed_name}: {strikes} consecutive failed 429 cycles — "
                        f"cooldown {self._429_COOLDOWN_SECONDS}s"
                    )
                else:
                    logger.warning(
                        f"Feed {feed_name} 429 cycle {strikes}/{self._429_STRIKE_LIMIT} — "
                        f"will enter cooldown if persistent"
                    )
            raise

    @retry_with_backoff(max_retries=3, initial_delay=2)
    def _poll_feed_with_retry(self, feed_name, feed_url):
        """
        Fetch + parse a single RSS feed (retries live in the decorator).

        Args:
            feed_name: Name of the feed
            feed_url: URL of the RSS feed

        Returns:
            list: List of entry dictionaries
        """
        logger.debug(f"Polling RSS feed: {feed_name}")
        
        try:
            # Fetch with an explicit per-request timeout instead of a process-global
            # socket.setdefaulttimeout (which leaked onto every other socket in the
            # process during the poll window). feedparser then parses the bytes.
            response = requests.get(
                feed_url, timeout=30, headers={'User-Agent': 'newsbot/1.0 (+rss)'}
            )
            response.raise_for_status()
            feed = feedparser.parse(response.content)

            if feed.bozo:
                logger.warning(f"Feed parsing warning for {feed_name}: {feed.bozo_exception}")
            
            logger.debug(f"RSS feed {feed_name} contains {len(feed.entries)} raw entries")
            
            entries = []
            skipped_stale = 0
            skipped_parse = 0

            for entry in feed.entries:
                parsed_entry, skip_reason = self._parse_entry(entry, feed_name)
                if parsed_entry:
                    entries.append(parsed_entry)
                elif skip_reason == "stale":
                    skipped_stale += 1
                else:
                    skipped_parse += 1

            detail = f" ({skipped_parse} skipped due to parsing errors)" if skipped_parse else ""
            stale_detail = f", {skipped_stale} stale" if skipped_stale else ""
            logger.info(f"Found {len(entries)} entries in {feed_name}{stale_detail}{detail}")
            if entries:
                logger.debug(f"Entry IDs from {feed_name}: {[e['id'] for e in entries]}")
            
            return entries
            
        except Exception as e:
            logger.error(f"Error polling feed {feed_name}: {e}")
            raise
    
    def _parse_entry(self, entry, feed_name):
        """
        Parse a single feed entry
        
        Args:
            entry: Feed entry object
            feed_name: Name of the source feed
        
        Returns:
            dict: Parsed entry data or None if invalid
        """
        try:
            # Extract basic information
            title = entry.get('title', '').strip()
            description = entry.get('description', '').strip()
            # Canonicalize before anything else reads it: `link` is what gets
            # handed to gallery-dl and shown by the Source command, and a Nitter
            # feed emits its own host, not x.com.
            link = self._canonicalize_link(entry.get('link', '').strip())
            
            # Get publication date
            pub_date = entry.get('published', entry.get('updated', ''))

            # Freshness guard: DB retention can outlive a slow feed's item list,
            # so an old item still present in the feed would otherwise be
            # re-posted once its processed/embedding rows expire. Items with
            # missing or unparseable dates are processed normally.
            max_age_hours = getattr(config, 'RSS_MAX_ENTRY_AGE_HOURS', 0)
            if max_age_hours and pub_date:
                try:
                    age_hours = (time.time() - parsedate_to_datetime(pub_date).timestamp()) / 3600
                    if age_hours > max_age_hours:
                        logger.debug(
                            f"Skipping stale RSS entry ({age_hours:.1f}h old, "
                            f"max {max_age_hours}h): {link}"
                        )
                        return None, "stale"
                except (TypeError, ValueError):
                    pass

            # Extract Twitter status ID from link
            status_id = self._extract_status_id(link)
            
            if not status_id:
                logger.warning(f"Could not extract status ID from: {link}")
                return None, "no_status_id"
            
            # Create unique ID
            entry_id = f"twitter_{status_id}"
            
            # Store content from RSS as fallback (will be replaced by gallery-dl if successful)
            # Prefer description over title as description typically has full tweet text
            # Title in RSS feeds is often truncated
            # This is only used if gallery-dl fails to extract the full text from x.com
            
            # IMPORTANT: Extract full URLs from HTML anchor tags BEFORE cleaning
            # RSS feeds contain truncated URLs in display text but full URLs in href attributes
            # Example: <a href="https://full-url.com/path">truncated…</a>
            content = description if description else title
            content = extract_urls_from_html(content)
            
            # Clean HTML tags (blockquote, p, etc.) and remove Twitter attribution
            # This prevents raw HTML from appearing in Discord posts
            content = clean_text_content(content)
            content = remove_twitter_attribution(content)
            
            # Extract media URLs if present
            media_urls = self._extract_media_urls(entry)
            
            parsed = {
                'id': entry_id,
                'status_id': status_id,
                'source': feed_name,
                'source_type': 'twitter',
                'title': title,
                'content': content,
                'link': link,
                'pub_date': pub_date,
                'media_urls': media_urls
            }
            
            logger.debug(f"Parsed entry: {entry_id} - {title[:50]}...")
            return parsed, "ok"
            
        except Exception as e:
            logger.error(f"Error parsing entry from {feed_name}: {e}")
            return None, "error"
    
    def _canonicalize_link(self, url):
        """
        Rewrite a feed's tweet permalink to its canonical x.com form.

        The feed provider decides the host in the <link> element. rss.app emits
        x.com URLs directly, but a Nitter instance emits its own host plus an
        anchor: https://nitter.net/WatcherGuru/status/2082883530426601713#m

        That matters because `link` is passed straight to gallery-dl
        (media_handler.download_twitter_media) and surfaced by the Source
        context command — gallery-dl's twitter extractor can't parse a
        non-x.com host, and users shouldn't be shown an instance URL.

        Deliberately host-agnostic rather than a nitter->x.com replacement: the
        same code then works against rss.app, a public Nitter instance, or a
        self-hosted one, so switching providers is a config change with no code
        change to revert. Idempotent on links that are already canonical.

        Args:
            url: Tweet permalink as published by the feed

        Returns:
            str: https://x.com/<handle>/status/<id>, or the input unchanged if
                 it doesn't look like a tweet permalink
        """
        match = re.search(r'/([^/]+)/status(?:es)?/(\d+)', url)
        if not match:
            return url

        return f"https://x.com/{match.group(1)}/status/{match.group(2)}"

    def _extract_status_id(self, url):
        """
        Extract Twitter status ID from URL
        
        Args:
            url: Twitter URL
        
        Returns:
            str: Status ID or None
        """
        # Match patterns like twitter.com/user/status/1234567890
        # or x.com/user/status/1234567890
        patterns = [
            r'/status/(\d+)',
            r'/statuses/(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return None
    
    def _extract_media_urls(self, entry):
        """
        Extract media URLs from RSS entry
        
        Args:
            entry: Feed entry object
        
        Returns:
            list: List of media URLs
        """
        media_urls = []
        
        # Check for media:content tags
        if hasattr(entry, 'media_content'):
            for media in entry.media_content:
                url = media.get('url')
                if url:
                    media_urls.append(url)
        
        # Check for enclosures
        if hasattr(entry, 'enclosures'):
            for enclosure in entry.enclosures:
                url = enclosure.get('href')
                if url:
                    media_urls.append(url)
        
        return media_urls
    
    def poll_all_feeds(self):
        """
        Poll all configured RSS feeds in parallel using a thread pool.

        Each feed is fetched with a per-request 30s timeout (see poll_feed) and
        polled in its own thread, so total time is max-of-all-feeds instead of sum.

        Returns:
            list: Combined list of all entries from all feeds
        """
        logger.info(f"Polling {len(self.feeds)} RSS feeds...")

        all_entries = []
        with ThreadPoolExecutor(max_workers=len(self.feeds)) as executor:
            futures = {
                executor.submit(self.poll_feed, name, url): name
                for name, url in self.feeds.items()
            }
            for future in as_completed(futures):
                feed_name = futures[future]
                try:
                    entries = future.result(timeout=35)
                    all_entries.extend(entries)
                except Exception as e:
                    logger.error(f"Failed to poll feed {feed_name}: {e}")

        logger.info(f"Total RSS entries collected: {len(all_entries)}")
        return all_entries

