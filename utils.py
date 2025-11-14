"""
Utility functions for the Discord News Aggregator Bot
"""
import logging
import time
import os
import shutil
import sys
from functools import wraps
from pathlib import Path

# Set up logging
def setup_logging():
    """Configure logging with debug level for comprehensive diagnostics"""
    # Configure file handler
    file_handler = logging.FileHandler('bot.log', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
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
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # Configure root logger
    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[file_handler, console_handler]
    )
    
    return logging.getLogger(__name__)

logger = setup_logging()

def retry_with_backoff(max_retries=3, initial_delay=2):
    """
    Decorator for retrying functions with exponential backoff
    
    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds (doubles on each retry)
    """
    def decorator(func):
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
    
    # Remove x.com and twitter.com URLs (with or without https://)
    # Matches patterns like:
    # - x.com/username/status/1234567890
    # - twitter.com/username/status/1234567890
    # - https://x.com/username/status/1234567890
    # - https://twitter.com/username/status/1234567890
    url_pattern = r'https?://(?:www\.)?(?:x\.com|twitter\.com)/\S+|(?:^|\s)(?:x\.com|twitter\.com)/\S+'
    text = re.sub(url_pattern, '', text)
    
    # Clean up any multiple consecutive spaces left behind
    text = re.sub(r' +', ' ', text)
    
    # Clean up trailing/leading whitespace
    text = text.strip()
    
    return text

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

