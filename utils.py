"""
Utility functions for the Discord News Aggregator Bot
"""
import logging
import time
import os
import shutil
import sys
import asyncio
import inspect
from functools import wraps
from pathlib import Path

# Set up logging
def setup_logging():
    """Configure logging with debug level for comprehensive diagnostics"""
    # Configure console handler with UTF-8 encoding
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)

    # Set UTF-8 encoding for console on Windows
    if sys.platform == 'win32':
        try:
            # Reconfigure stdout to use UTF-8
            sys.stdout.reconfigure(encoding='utf-8')
        except AttributeError:
            # Python < 3.7, use a different approach
            import codecs
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)

    handlers = [console_handler]
    # Configure file handler — may fail when bot.log is already locked by another
    # process (e.g. the main bot holds it while the eval subprocess runs).
    try:
        file_handler = logging.FileHandler('bot.log', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except PermissionError:
        pass  # console-only logging; caller's stdout redirection captures output

    # Configure root logger
    logging.basicConfig(
        level=logging.DEBUG,
        handlers=handlers
    )
    
    return logging.getLogger(__name__)

logger = setup_logging()

def retry_with_backoff(max_retries=3, initial_delay=2):
    """
    Decorator for retrying functions with exponential backoff.
    Automatically detects async functions and uses await/asyncio.sleep
    instead of blocking time.sleep.

    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds (doubles on each retry)
    """
    def decorator(func):
        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                delay = initial_delay
                last_exception = None

                for attempt in range(max_retries + 1):
                    try:
                        return await func(*args, **kwargs)
                    except Exception as e:
                        last_exception = e
                        if attempt < max_retries:
                            logger.warning(
                                f"Attempt {attempt + 1}/{max_retries + 1} failed for {func.__name__}: {e}. "
                                f"Retrying in {delay}s..."
                            )
                            await asyncio.sleep(delay)
                            delay *= 2
                        else:
                            logger.error(
                                f"All {max_retries + 1} attempts failed for {func.__name__}: {e}",
                                exc_info=True
                            )

                raise last_exception
            return async_wrapper
        else:
            @wraps(func)
            def wrapper(*args, **kwargs):
                delay = initial_delay
                last_exception = None

                for attempt in range(max_retries + 1):
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        last_exception = e
                        if attempt < max_retries:
                            logger.warning(
                                f"Attempt {attempt + 1}/{max_retries + 1} failed for {func.__name__}: {e}. "
                                f"Retrying in {delay}s..."
                            )
                            time.sleep(delay)
                            delay *= 2
                        else:
                            logger.error(
                                f"All {max_retries + 1} attempts failed for {func.__name__}: {e}",
                                exc_info=True
                            )

                raise last_exception
            return wrapper
    return decorator

def cleanup_temp_files(temp_dir):
    """
    Clean up temporary files in a directory
    
    Args:
        temp_dir: Path to temporary directory
    """
    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            logger.debug(f"Cleaned up temporary directory: {temp_dir}")
    except Exception as e:
        logger.error(f"Error cleaning up temp directory {temp_dir}: {e}")


def cleanup_old_media_files(media_dir="temp_media", retention_days=2):
    """
    Clean up media files older than retention_days
    
    Args:
        media_dir: Path to media directory (default: "temp_media")
        retention_days: Number of days to keep files (default: 2)
    """
    try:
        media_path = Path(media_dir)
        if not media_path.exists():
            return
        
        cutoff_time = time.time() - (retention_days * 24 * 3600)
        deleted_count = 0
        
        # Iterate through all subdirectories in temp_media
        for entry_dir in media_path.iterdir():
            if entry_dir.is_dir():
                # Check the modification time of the directory
                # Use the most recent file modification time as the directory age
                dir_mtime = entry_dir.stat().st_mtime
                
                # Also check files in the directory
                file_mtimes = []
                for file_path in entry_dir.rglob("*"):
                    if file_path.is_file():
                        file_mtimes.append(file_path.stat().st_mtime)
                
                # Use the most recent modification time (directory or any file)
                most_recent = max([dir_mtime] + file_mtimes) if file_mtimes else dir_mtime
                
                # Delete if older than retention period
                if most_recent < cutoff_time:
                    try:
                        shutil.rmtree(entry_dir)
                        deleted_count += 1
                        logger.debug(f"Cleaned up old media directory: {entry_dir}")
                    except Exception as e:
                        logger.error(f"Error cleaning up media directory {entry_dir}: {e}")
        
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old media directories (older than {retention_days} days)")
        
    except Exception as e:
        logger.error(f"Error cleaning up old media files: {e}")


def cleanup_bot_log(log_path: str = 'bot.log', max_age_hours: int = 24) -> None:
    import re
    from datetime import datetime, timedelta
    if not os.path.exists(log_path):
        return
    cutoff = datetime.now() - timedelta(hours=max_age_hours)
    ts_re = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)')
    kept, include_block = [], False
    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            m = ts_re.match(line)
            if m:
                try:
                    include_block = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S,%f') >= cutoff
                except ValueError:
                    include_block = True
            if include_block:
                kept.append(line)
    # On Windows os.replace() fails while the logging FileHandler holds bot.log open.
    # Close all FileHandlers pointing to this path, overwrite in place, then reopen them.
    abs_path = os.path.abspath(log_path)
    open_handlers = [
        h for h in logging.getLogger().handlers
        if isinstance(h, logging.FileHandler) and os.path.abspath(h.baseFilename) == abs_path
    ]
    for h in open_handlers:
        h.close()
    try:
        with open(log_path, 'w', encoding='utf-8') as f:
            f.writelines(kept)
    finally:
        for h in open_handlers:
            h.stream = open(h.baseFilename, h.mode, encoding='utf-8')
    logger.info(f"Log cleanup complete: retained {len(kept)} lines (last {max_age_hours}h)")


def ensure_directory(directory):
    """
    Ensure a directory exists, create it if it doesn't
    
    Args:
        directory: Path to directory
    """
    Path(directory).mkdir(parents=True, exist_ok=True)

def get_temp_dir():
    """
    Get a temporary directory for media downloads
    
    Returns:
        Path to temporary directory
    """
    temp_dir = os.path.join(os.getcwd(), 'temp_media')
    ensure_directory(temp_dir)
    return temp_dir

def extract_urls_from_html(text):
    """
    Extract full URLs from HTML anchor tags, replacing truncated display text
    
    This handles RSS feeds that contain:
    <a href="https://full-url.com/path">truncated-url.com/pa…</a>
    
    And converts them to just the full URL:
    https://full-url.com/path
    
    Args:
        text: Text containing HTML anchor tags
    
    Returns:
        str: Text with full URLs extracted from href attributes
    """
    if not text:
        return text
    
    import re
    
    # Pattern to match anchor tags: <a href="URL">display text</a>
    # This handles both single and double quotes, and various attributes
    anchor_pattern = r'<a\s+(?:[^>]*?\s+)?href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>'
    
    def replace_anchor(match):
        href_url = match.group(1)  # The full URL from href attribute
        display_text = match.group(2)  # The display text (potentially truncated)
        
        # If display text contains ellipsis (… or ...), it's likely truncated
        # Replace with the full href URL
        if '…' in display_text or '...' in display_text:
            return href_url
        
        # If display text looks like a URL but is shorter than href, use href
        # This handles cases where text is truncated without explicit ellipsis
        if display_text.startswith(('http://', 'https://', 'www.')) and len(display_text) < len(href_url):
            return href_url
        
        # If display text doesn't start with http but looks like a domain
        # and href is longer, prefer href
        if '.' in display_text and not display_text.startswith(('http://', 'https://')) and href_url.startswith(('http://', 'https://')):
            # Check if display text appears to be truncated version of href
            if display_text.replace('www.', '') in href_url:
                return href_url
        
        # Otherwise keep the display text (it's likely a descriptive link text)
        return display_text
    
    # Replace all anchor tags with extracted URLs or display text
    text = re.sub(anchor_pattern, replace_anchor, text)
    
    return text

def resolve_shortened_urls(text):
    """
    Resolve shortened URLs (like t.co) to their full URLs
    
    Args:
        text: Text containing potentially shortened URLs
    
    Returns:
        str: Text with resolved URLs
    """
    import re
    import requests
    
    # Find all t.co URLs
    url_pattern = r'https?://t\.co/\w+'
    urls = re.findall(url_pattern, text)
    
    for short_url in urls:
        try:
            # Follow redirects to get the final URL
            response = requests.head(short_url, allow_redirects=True, timeout=5)
            final_url = response.url
            
            # Replace the shortened URL with the final URL
            text = text.replace(short_url, final_url)
            logger.debug(f"Resolved {short_url} -> {final_url}")
            
        except Exception as e:
            logger.warning(f"Could not resolve URL {short_url}: {e}")
            # Keep the original URL if resolution fails
            continue
    
    return text

def shorten_dexerto_url_in_text(text: str) -> str:
    """Replace the first dexerto.com URL in text with a TinyURL. No-op on failure."""
    import re
    import requests
    match = re.search(r'https?://(?:www\.)?dexerto\.com/\S+', text)
    if not match:
        return text
    original_url = match.group(0).rstrip(')')
    try:
        resp = requests.get(
            "https://tinyurl.com/api-create.php",
            params={"url": original_url},
            timeout=5,
        )
        if resp.status_code == 200 and resp.text.strip().startswith("https://tinyurl.com"):
            return text.replace(original_url, resp.text.strip())
    except Exception as e:
        logger.warning(f"TinyURL shortening failed for {original_url}: {e}")
    return text

_tinyurl_cache: dict[str, str] = {}

def shorten_urls_in_text(text: str) -> str:
    """Replace all HTTP URLs in text with TinyURLs, skipping video.twimg.com and tinyurl.com."""
    import re
    import requests

    urls = re.findall(r'https?://\S+', text)
    seen: set[str] = set()
    for raw in urls:
        url = raw.rstrip(')')
        if url in seen:
            continue
        seen.add(url)
        if 'video.twimg.com' in url or 'tinyurl.com' in url:
            continue
        if url in _tinyurl_cache:
            short = _tinyurl_cache[url]
            if short != url:
                text = text.replace(url, short)
            continue
        try:
            resp = requests.get(
                "https://tinyurl.com/api-create.php",
                params={"url": url},
                timeout=5,
            )
            if resp.status_code == 200 and resp.text.strip().startswith("https://tinyurl.com"):
                short = resp.text.strip()
                _tinyurl_cache[url] = short
                text = text.replace(url, short)
            else:
                _tinyurl_cache[url] = url
        except Exception as e:
            logger.warning(f"TinyURL shortening failed for {url}: {e}")
            _tinyurl_cache[url] = url
    return text

def clean_text_content(text):
    """
    Clean text content by stripping HTML and normalizing whitespace
    Converts HTML block elements to newlines to preserve structure
    
    Args:
        text: Text to clean
    
    Returns:
        str: Cleaned text
    """
    if not text:
        return text
    
    import re
    
    # First, replace block-level HTML tags with newlines to preserve text structure
    # This ensures that <p>Text1</p><p>Text2</p> becomes "Text1\nText2" not "Text1Text2"
    block_tags = r'</?(p|div|br|h[1-6]|ul|ol|li|blockquote|pre)[^>]*>'
    text = re.sub(block_tags, '\n', text, flags=re.IGNORECASE)
    
    # Now remove any remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    
    # Decode common HTML entities
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    text = text.replace('&amp;', '&')
    text = text.replace('&quot;', '"')
    text = text.replace('&#39;', "'")
    text = text.replace('&nbsp;', ' ')
    
    # Split into lines and strip whitespace from each line
    lines = [line.strip() for line in text.split('\n')]
    
    # Remove empty lines - we only want content lines with single newlines between them
    cleaned_lines = [line for line in lines if line]
    
    # Join back together with single newlines
    cleaned_text = '\n'.join(cleaned_lines)
    
    # Strip leading and trailing whitespace
    cleaned_text = cleaned_text.strip()
    
    return cleaned_text

def remove_emojis(text):
    """
    Remove all emoji characters from text and clean up leftover whitespace
    
    Args:
        text: Text containing emojis
    
    Returns:
        str: Text with emojis removed and whitespace normalized
    """
    if not text:
        return text
    
    import re
    
    # Comprehensive emoji pattern covering various Unicode ranges
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U00002702-\U000027B0"  # dingbats
        "\U000024C2-\U0001F251"  # enclosed characters
        "\U0001F900-\U0001F9FF"  # supplemental symbols and pictographs
        "\U0001FA00-\U0001FA6F"  # chess symbols
        "\U0001FA70-\U0001FAFF"  # symbols and pictographs extended-a
        "\U00002600-\U000026FF"  # miscellaneous symbols
        "\U00002700-\U000027BF"  # dingbats
        "\U0001F018-\U0001F270"  # various symbols
        "\U0001F300-\U0001F5FF"  # misc symbols and pictographs
        "]+",
        flags=re.UNICODE
    )
    
    # Remove emojis
    text = emoji_pattern.sub('', text)
    
    # Clean up multiple consecutive spaces left behind
    text = re.sub(r' +', ' ', text)
    
    # Clean up spaces at the start and end of lines
    lines = text.split('\n')
    cleaned_lines = [line.strip() for line in lines if line.strip()]
    text = '\n'.join(cleaned_lines)
    
    # Strip leading and trailing whitespace
    text = text.strip()
    
    return text

def remove_corrupted_emoji_marks(text):
    """
    Remove question marks that are corrupted emoji characters.
    These appear as standalone question marks where emojis used to be.
    
    Examples:
        "?NEW: @unruggable_io..." -> "NEW: @unruggable_io..."
        "?? The Seeker Airdrop..." -> "The Seeker Airdrop..."
        "JUST IN: ?? SoFi becomes..." -> "JUST IN: SoFi becomes..."
    
    Args:
        text: Text potentially containing corrupted emoji question marks
    
    Returns:
        str: Text with corrupted emoji question marks removed
    """
    if not text:
        return text
    
    import re
    
    # Remove question marks at the start of text
    text = re.sub(r'^\?+', '', text)
    
    # Remove question marks after whitespace and before a capital letter
    # This catches patterns like " ?NEW:" or " ?? SoFi"
    text = re.sub(r'\s+\?+([A-Z])', r' \1', text)
    
    # Remove question marks that appear after a colon and before a capital letter
    # This catches patterns like "JUST IN: ?? SoFi"
    text = re.sub(r':\s*\?+([A-Z])', r': \1', text)
    
    # Remove multiple consecutive question marks anywhere (but preserve single ? in context)
    # Only remove if they're standalone (surrounded by spaces or at start/end)
    text = re.sub(r'\s+\?{2,}\s+', ' ', text)  # Multiple ? with spaces around
    text = re.sub(r'^\?{2,}\s+', '', text)  # Multiple ? at start
    text = re.sub(r'\s+\?{2,}$', '', text)  # Multiple ? at end
    
    # Clean up any double spaces left behind
    text = re.sub(r' +', ' ', text)
    
    # Strip leading and trailing whitespace
    text = text.strip()
    
    return text

def strip_wire_prefixes(text):
    """
    Remove wire-service style prefix labels from the start of content.
    These add no information and clutter Discord posts.

    Examples:
        "JUST IN: Bitcoin hits $71,000" -> "Bitcoin hits $71,000"
        "JUST IN : Bitcoin hits $70,000" -> "Bitcoin hits $70,000"
        "BREAKING: Google says a new iOS..." -> "Google says a new iOS..."
        "NEW: @Yield expands to @solana..." -> "@Yield expands to @solana..."
        "NEW : FBI and Thai police seized..." -> "FBI and Thai police seized..."

    Args:
        text: Text potentially starting with a wire-service prefix

    Returns:
        str: Text with prefix removed
    """
    if not text:
        return text

    import re

    # Strip leading emojis/symbols before checking for prefixes
    # Handles cases like "🚨JUST IN:" or "🚨 BREAKING:"
    text = re.sub(r'^[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u26A0\u203C\u2757\u2755\u274C\u274E\u2705\u267B\uFE0F]+\s*', '', text)

    # Match common wire-service prefixes at the start of text
    # Handles optional space before colon: "JUST IN:" and "JUST IN :"
    # Case-insensitive to catch "New:", "Breaking:", etc.
    text = re.sub(
        r'^(JUST IN|BREAKING|NEW|DEVELOPING|EXCLUSIVE|ALERT|FLASH|URGENT|UPDATE|REPORT|WATCH|LIVE|SCOOP|HAPPENING NOW)\s*:\s*',
        '',
        text,
        flags=re.IGNORECASE
    )

    text = text.strip()

    return text


def is_audience_question(content: str) -> bool:
    """
    Return True if content ends with an audience-engagement question.

    Detects patterns like "Do you agree?", "What do you think?", "Thoughts?"
    that solicit reader opinions rather than conveying news.

    Examples that trigger:
        '...Larry Fink has said. Do you agree?'        -> True
        'Bitcoin hits $100k. What do you think?'       -> True
        'Markets up 3%. Thoughts?'                     -> True
        'Are you buying the dip?'                      -> True

    Examples that do NOT trigger:
        'Will the Fed raise rates this month?'         -> False (no 'you')
        'He asked if investors would agree.'           -> False (no trailing ?)
        'Analysts are cautious about the outlook.'     -> False
    """
    import re

    if not content:
        return False

    # Split on sentence-ending punctuation, grab the last sentence
    sentences = re.split(r'(?<=[.!?])\s+', content.strip())
    last = sentences[-1].strip() if sentences else ''

    if not last.endswith('?'):
        return False

    _PATTERN = re.compile(
        r'^('
        r'do you |'
        r'what do you |'
        r'what are your |'
        r'are you |'
        r'would you |'
        r'will you |'
        r'how do you |'
        r'how would you |'
        r'did you |'
        r'have you |'
        r'could you |'
        r'should you |'
        r'agree\?$|'
        r'disagree\?$|'
        r'thoughts\?$|'
        r'your thoughts\?|'
        r'your take\?|'
        r'your opinion\?'
        r')',
        re.IGNORECASE
    )
    return bool(_PATTERN.search(last))


def format_quote_tweets(text):
    """
    Detect and reformat quote-tweet content from gallery-dl output.

    gallery-dl concatenates quote tweet text with the quoted tweet's
    author and content without any separator, e.g.:
        "TRUMP: CUBA IS IN BIG TROUBLENewsWire (@NewsWire_US) TRUMP ON CUBA: ..."
    becomes:
        "TRUMP: CUBA IS IN BIG TROUBLE
         TRUMP ON CUBA: ..."

    The author attribution is removed and trailing em dashes are stripped.

    Args:
        text: Tweet text potentially containing concatenated quote-tweet content

    Returns:
        str: Text with quote-tweet parts properly separated and cleaned
    """
    if not text:
        return text

    import re

    # Find all (@handle) patterns
    handle_matches = list(re.finditer(r'\(@(\w+)\)', text))
    if not handle_matches:
        return text

    # Process from right to left to preserve string positions
    for handle_match in reversed(handle_matches):
        handle_start = handle_match.start()
        handle_end = handle_match.end()

        # Check if there's substantial text after the handle (not just a date)
        after = text[handle_end:].strip()
        if len(after) < 15:
            continue  # Likely attribution at end, skip
        if re.match(r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)', after):
            continue  # Date follows handle, this is attribution not a quote tweet

        # Get text before the handle
        before = text[:handle_start]

        # Find the last word before the handle (the author name)
        name_match = re.search(r'(\S+)\s*$', before)
        if not name_match:
            continue

        name_start = name_match.start()

        # Check if there's NO space between the preceding text and the author name
        # This is the key signal: "TROUBLENewsWire" vs "TROUBLE NewsWire"
        if name_start > 0 and before[name_start - 1].isalnum():
            # Remove the author attribution "AuthorName (@handle) " and insert newline
            before_text = text[:name_start].rstrip()
            after_text = text[handle_end:].lstrip()

            # Strip trailing em dashes and hyphens from the quoted content
            after_text = re.sub(r'[—\-]+\s*$', '', after_text).rstrip()

            text = before_text + '\n' + after_text

    return text


def remove_twitter_attribution(text):
    """
    Remove Twitter attribution (author/handle/date) from the end of tweets
    
    Examples:
        "Text content.— Watcher.Guru (@WatcherGuru) October 23, 2025" 
        -> "Text content."
        
        "Text content — CBPP— NewsWire (@NewsWire_US) Oct 23, 2025"
        -> "Text content — CBPP"
        
        "Text content NewsWire (@NewsWire_US) Oct 31, 2025"
        -> "Text content"
    
    Args:
        text: Tweet text potentially containing attribution
    
    Returns:
        str: Text with Twitter attribution removed
    """
    if not text:
        return text
    
    import re
    
    # Strategy 1: Try to match the full attribution pattern at the end
    # Matches patterns like: "Author Name (@handle) Month Day, Year"
    # or "Author Name (@handle) Mon Day, Year"
    # Covers various formats: October 31, 2025 / Oct 31, 2025 / Oct 31 2025
    # Also includes optional dashes (— or -) before the attribution
    full_attribution_pattern = r'[—\-]?\s*[\w\s\.\-]+\(@\w+\)\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\s*$'
    match = re.search(full_attribution_pattern, text)
    
    if match:
        # Remove the matched attribution
        cleaned_text = text[:match.start()].rstrip()
        return cleaned_text
    
    # Strategy 2: Look for a dash before the handle (original logic)
    # Find all Twitter handles in the format (@username)
    handle_pattern = r'\(@\w+\)'
    handle_matches = list(re.finditer(handle_pattern, text))
    
    # If no handles found, return text as-is
    if not handle_matches:
        return text
    
    # Get the position of the last handle (attribution is at the end)
    last_handle = handle_matches[-1]
    handle_start = last_handle.start()
    
    # Get the text before the handle
    text_before_handle = text[:handle_start]
    
    # Find all em dashes (—) and regular dashes/hyphens (-) before the handle
    # We look for em dashes that could mark the start of the attribution
    dash_matches = list(re.finditer(r'[—\-]', text_before_handle))
    
    # If no dashes found, return text as-is
    if not dash_matches:
        return text
    
    # Get the position of the last dash before the handle
    last_dash = dash_matches[-1]
    attribution_start = last_dash.start()
    
    # Remove everything from the last dash onwards
    cleaned_text = text[:attribution_start].rstrip()
    
    return cleaned_text

def remove_xcom_urls(text):
    """
    Remove x.com and twitter.com URLs from text (for embedded tweet URLs)
    
    Examples:
        "It's important to liberate Venezuela x.com/NewsWire_US/status/1981774534635397623"
        -> "It's important to liberate Venezuela"
        
        "Check this out twitter.com/user/status/123456"
        -> "Check this out"
    
    Args:
        text: Text potentially containing x.com or twitter.com URLs
    
    Returns:
        str: Text with x.com and twitter.com URLs removed
    """
    if not text:
        return text
    
    import re
    
    # Remove x.com, twitter.com, and pic.twitter.com URLs (with or without https://)
    # Matches patterns like:
    # - x.com/username/status/1234567890
    # - twitter.com/username/status/1234567890
    # - https://x.com/username/status/1234567890
    # - https://twitter.com/username/status/1234567890
    # - pic.twitter.com/Abc123XYZ
    url_pattern = r'https?://(?:www\.)?(?:pic\.twitter\.com|x\.com|twitter\.com)/\S+|(?:^|\s)(?:pic\.twitter\.com|x\.com|twitter\.com)/\S+'
    text = re.sub(url_pattern, '', text)
    
    # Clean up any multiple consecutive spaces left behind
    text = re.sub(r' +', ' ', text)
    
    # Clean up trailing/leading whitespace
    text = text.strip()
    
    return text

def ensure_url_on_own_line(text):
    """
    Ensure URLs that are directly preceded by non-whitespace are placed on their own line.
    
    This fixes cases where emoji removal or text cleaning causes URLs to be
    glued to the preceding text, e.g.:
        "raised the entry age to 25https://example.com"
    becomes:
        "raised the entry age to 25
        https://example.com"
    
    Args:
        text: Text potentially containing URLs glued to preceding text
    
    Returns:
        str: Text with URLs properly separated on their own line
    """
    if not text:
        return text
    
    import re
    
    # Insert a newline before any URL (http:// or https://) that is directly
    # preceded by a non-whitespace character
    text = re.sub(r'(\S)(https?://)', r'\1\n\2', text)
    
    return text


def is_all_caps(text, threshold=0.65):
    """
    Detect if text is predominantly ALL CAPS (wire-service style).

    Only counts alphabetic characters. Ignores numbers, URLs, punctuation,
    and whitespace. Returns True if the ratio of uppercase letters to
    total letters exceeds the threshold.

    Args:
        text: Text to check
        threshold: Ratio of uppercase chars needed (default: 0.65 = 65%)

    Returns:
        bool: True if text is predominantly ALL CAPS
    """
    if not text:
        return False

    import re

    # Remove URLs before checking (URLs contain mixed case that would skew the ratio)
    text_without_urls = re.sub(r'https?://\S+', '', text)

    # Count only alphabetic characters
    alpha_chars = [c for c in text_without_urls if c.isalpha()]

    if len(alpha_chars) < 10:
        return False

    upper_count = sum(1 for c in alpha_chars if c.isupper())
    ratio = upper_count / len(alpha_chars)

    return ratio >= threshold


def remove_telegram_formatting(text, channel_name=None):
    """
    Remove Telegram formatting markup and channel usernames
    
    - Removes all ** bold markers
    - Removes @News_Crypto and @Fin_Watch username lines at the end
    
    Examples:
        "**JUST IN: ******** Text**\n@News_Crypto" 
        -> "JUST IN: Text"
    
    Args:
        text: Text to clean
        channel_name: Name of the Telegram channel (optional, for specific cleanup)
    
    Returns:
        str: Text with formatting removed
    """
    if not text:
        return text
    
    import re
    
    # Remove all ** bold markers
    text = text.replace('**', '')
    
    # Split into lines
    lines = text.split('\n')
    
    # Filter out lines that are just channel usernames for specific channels
    # This removes lines like "@News_Crypto" and "@Fin_Watch"
    filtered_lines = []
    for line in lines:
        line_stripped = line.strip()
        # Skip lines that are exactly the channel username
        if line_stripped in ['@News_Crypto', '@Fin_Watch']:
            continue
        filtered_lines.append(line)
    
    # Join back together
    text = '\n'.join(filtered_lines)
    
    # Clean up any extra whitespace
    text = text.strip()
    
    return text

