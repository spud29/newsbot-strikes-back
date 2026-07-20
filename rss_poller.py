"""
RSS feed poller for Twitter feeds
"""
import feedparser
import re
import socket
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
        logger.info(f"RSS Poller initialized with {len(self.feeds)} feeds")
    
    @retry_with_backoff(max_retries=3, initial_delay=2)
    def poll_feed(self, feed_name, feed_url):
        """
        Poll a single RSS feed
        
        Args:
            feed_name: Name of the feed
            feed_url: URL of the RSS feed
        
        Returns:
            list: List of entry dictionaries
        """
        logger.debug(f"Polling RSS feed: {feed_name}")
        
        try:
            feed = feedparser.parse(feed_url)
            
            if feed.bozo:
                logger.warning(f"Feed parsing warning for {feed_name}: {feed.bozo_exception}")
            
            logger.debug(f"RSS feed {feed_name} contains {len(feed.entries)} raw entries")
            
            entries = []
            skipped = 0
            
            for entry in feed.entries:
                parsed_entry = self._parse_entry(entry, feed_name)
                if parsed_entry:
                    entries.append(parsed_entry)
                else:
                    skipped += 1
            
            logger.info(f"Found {len(entries)} entries in {feed_name} ({skipped} skipped due to parsing errors)")
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
            link = entry.get('link', '').strip()
            
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
                        return None
                except (TypeError, ValueError):
                    pass

            # Extract Twitter status ID from link
            status_id = self._extract_status_id(link)
            
            if not status_id:
                logger.warning(f"Could not extract status ID from: {link}")
                return None
            
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
            return parsed
            
        except Exception as e:
            logger.error(f"Error parsing entry from {feed_name}: {e}")
            return None
    
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

        Sets a 30-second socket timeout to prevent feedparser.parse() from
        hanging indefinitely on unresponsive feeds. Each feed is polled in
        its own thread so total time is max-of-all-feeds instead of sum.

        Returns:
            list: Combined list of all entries from all feeds
        """
        logger.info(f"Polling {len(self.feeds)} RSS feeds...")

        # Protect against dead feeds hanging the bot indefinitely
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(30)

        all_entries = []
        try:
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
        finally:
            socket.setdefaulttimeout(old_timeout)

        logger.info(f"Total RSS entries collected: {len(all_entries)}")
        return all_entries

