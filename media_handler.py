"""
Media handler for downloading media from Twitter and Telegram
"""
import os
import json
import subprocess
import asyncio
from pathlib import Path
from utils import logger, retry_with_backoff, get_temp_dir, cleanup_temp_files, clean_text_content, resolve_shortened_urls, remove_emojis, remove_corrupted_emoji_marks, remove_twitter_attribution, remove_xcom_urls
import config
from ocr_handler import OCRHandler

class MediaHandler:
    """Handles media downloads from Twitter and Telegram"""
    
    def __init__(self, telegram_client=None):
        """
        Initialize media handler
        
        Args:
            telegram_client: TelegramPoller instance for Telegram downloads
        """
        self.telegram_client = telegram_client
        self.temp_dir = get_temp_dir()
        self.ocr_handler = OCRHandler()
        logger.info("Media handler initialized")
    
    @retry_with_backoff(max_retries=3, initial_delay=2)
    def download_twitter_media(self, entry):
        """
        Download Twitter media using gallery-dl
        
        Args:
            entry: RSS entry dictionary
        
        Returns:
            dict: Updated entry with media_files list and full_text
        """
        link = entry.get('link')
        
        if not link:
            logger.warning("No link provided for Twitter media download")
            return entry
        
        logger.debug(f"Downloading Twitter media from: {link}")
        
        try:
            # Create temporary directory for this download
            download_dir = os.path.join(self.temp_dir, f"twitter_{entry['status_id']}")
            os.makedirs(download_dir, exist_ok=True)
            
            # First, extract the full tweet text using gallery-dl --print
            # This works for both text-only and media tweets
            # PRIMARY METHOD: Use gallery-dl to get full text directly from x.com URL
            # Use --range 1 to only extract content once (not once per image)
            logger.debug(f"Extracting tweet text using gallery-dl from: {link}")
            text_cmd = [
                'gallery-dl',
                '--print', '{content}',
                '--range', '1',
                '--no-download',
                link
            ]
            
            text_result = subprocess.run(
                text_cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30
            )
            
            # Extract text from gallery-dl output
            # With --no-download, gallery-dl outputs the content once, preserving newlines in the tweet
            full_text = ''
            if text_result.returncode == 0 and text_result.stdout.strip():
                # Take the entire output as-is (tweets can contain multiple lines)
                raw_output = text_result.stdout.strip()
                logger.debug(f"Raw gallery-dl output ({len(raw_output)} chars): {raw_output[:500]}")
                
                # BUGFIX: gallery-dl sometimes outputs content twice
                # Check if the text is duplicated by splitting lines in half and comparing
                output_lines = raw_output.split('\n')
                if len(output_lines) >= 2 and len(output_lines) % 2 == 0:
                    # Check if the first half of lines equals the second half
                    mid = len(output_lines) // 2
                    first_half = output_lines[:mid]
                    second_half = output_lines[mid:]
                    
                    if first_half == second_half:
                        logger.warning(f"Detected duplicate content in gallery-dl output, using first half only")
                        full_text = '\n'.join(first_half)
                    else:
                        full_text = raw_output
                else:
                    full_text = raw_output
                
                if full_text:
                    logger.info(f"✓ Successfully extracted full text from x.com using gallery-dl ({len(full_text)} chars)")
                else:
                    logger.warning(f"gallery-dl returned empty content for: {link}")
            else:
                logger.warning(f"gallery-dl failed to extract text (return code: {text_result.returncode})")
                if text_result.stderr:
                    logger.debug(f"gallery-dl stderr: {text_result.stderr[:200]}")
            
            # FALLBACK: If gallery-dl didn't get text, fall back to RSS content
            if not full_text:
                logger.info(f"Falling back to RSS feed content")
                full_text = entry.get('content', '')
                # Apply basic cleaning to RSS fallback content
                full_text = clean_text_content(full_text)
                full_text = resolve_shortened_urls(full_text)
            
            # Extract video URLs using gallery-dl -g
            video_urls = []
            video_url_cmd = [
                'gallery-dl',
                '--range', '1',
                '-g',
                link
            ]
            
            video_url_result = subprocess.run(
                video_url_cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30
            )
            
            # Parse video URLs from output (one URL per line)
            if video_url_result.returncode == 0:
                for line in video_url_result.stdout.strip().split('\n'):
                    url = line.strip()
                    # Check if it's a video URL
                    if url and 'video.twimg.com' in url:
                        video_urls.append(url)
                        logger.debug(f"Found video URL: {url}")
            
            # Now download media files (images only, skip videos)
            # Videos are handled via video_urls to avoid Discord file size limits
            media_cmd = [
                'gallery-dl',
                '--dest', download_dir,
                '--filename', '{num:>03}.{extension}',
                '--no-mtime',
                '--filter', "extension not in ('mp4', 'mov', 'avi', 'mkv', 'webm')",
                link
            ]
            
            media_result = subprocess.run(
                media_cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=120
            )
            
            # Collect downloaded media files (images only, not videos)
            media_files = []
            
            for root, dirs, files in os.walk(download_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    
                    # Check if it's an image file (skip JSON files and videos)
                    # Videos are handled via video_urls to avoid Discord file size limits
                    ext = os.path.splitext(file)[1].lower()
                    if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                        media_files.append(file_path)
            
            # Clean the full text: remove empty lines, resolve shortened URLs, remove emojis, remove x.com URLs, and remove Twitter attribution
            full_text = clean_text_content(full_text)
            full_text = resolve_shortened_urls(full_text)
            full_text = remove_emojis(full_text)
            full_text = remove_corrupted_emoji_marks(full_text)
            full_text = remove_xcom_urls(full_text)
            full_text = remove_twitter_attribution(full_text)
            
            # Extract text from images using OCR
            ocr_text = ""
            if media_files:
                logger.debug(f"Running OCR on {len(media_files)} Twitter images...")
                ocr_text = self.ocr_handler.extract_text_from_images(media_files)
                if ocr_text:
                    logger.info(f"✓ OCR extracted {len(ocr_text)} characters from Twitter images")
            
            logger.debug("Assigning entry values...")
            entry['media_files'] = media_files
            entry['video_urls'] = video_urls
            entry['full_text'] = full_text
            entry['ocr_text'] = ocr_text
            entry['download_dir'] = download_dir
            logger.debug("Entry values assigned successfully")
            
            logger.info(f"Downloaded {len(media_files)} image files from Twitter, extracted {len(video_urls)} video URLs")
            logger.debug("Returning entry from download_twitter_media")
            return entry
            
        except subprocess.TimeoutExpired:
            logger.error("gallery-dl timed out")
            entry['media_files'] = []
            entry['ocr_text'] = ""
            return entry
        except Exception as e:
            logger.error(f"Error downloading Twitter media: {e}")
            entry['media_files'] = []
            entry['ocr_text'] = ""
            return entry
    
    async def download_telegram_media(self, entry):
        """
        Download Telegram media using Telethon
        
        Args:
            entry: Telegram message entry dictionary
        
        Returns:
            dict: Updated entry with media_files list
        """
        if not entry.get('has_media'):
            entry['media_files'] = []
            return entry
        
        logger.debug(f"Downloading Telegram media for: {entry['id']}")
        
        try:
            # Create temporary directory for this download
            download_dir = os.path.join(self.temp_dir, f"telegram_{entry['message_id']}")
            os.makedirs(download_dir, exist_ok=True)
            
            media_files = []
            video_urls = []
            
            # Check if this is an album
            if entry.get('is_album') and entry.get('album_messages'):
                # Download all media in the album
                for i, message in enumerate(entry['album_messages']):
                    if message.media:
                        file_path = await self._download_media_file(
                            message,
                            download_dir,
                            f"media_{i}"
                        )
                        if file_path:
                            media_files.append(file_path)
                            
                            # Check if it's a video
                            ext = os.path.splitext(file_path)[1].lower()
                            if ext in ['.mp4', '.mov', '.avi']:
                                # For Telegram videos, we'll just note them but can't get direct URL
                                video_urls.append(f"telegram_video_{i}")
            else:
                # Single media file
                message = entry.get('message_obj')
                if message and message.media:
                    file_path = await self._download_media_file(
                        message,
                        download_dir,
                        "media"
                    )
                    if file_path:
                        media_files.append(file_path)
                        
                        # Check if it's a video
                        ext = os.path.splitext(file_path)[1].lower()
                        if ext in ['.mp4', '.mov', '.avi']:
                            video_urls.append("telegram_video")
            
            # Extract text from images using OCR (skip videos)
            ocr_text = ""
            if media_files:
                # Filter to only image files for OCR
                image_files = [f for f in media_files if os.path.splitext(f)[1].lower() in ['.jpg', '.jpeg', '.png', '.gif', '.webp']]
                if image_files:
                    logger.debug(f"Running OCR on {len(image_files)} Telegram images...")
                    ocr_text = self.ocr_handler.extract_text_from_images(image_files)
                    if ocr_text:
                        logger.info(f"✓ OCR extracted {len(ocr_text)} characters from Telegram images")
            
            entry['media_files'] = media_files
            entry['video_urls'] = video_urls
            entry['ocr_text'] = ocr_text
            entry['download_dir'] = download_dir
            
            logger.info(f"Downloaded {len(media_files)} media files from Telegram")
            return entry
            
        except Exception as e:
            logger.error(f"Error downloading Telegram media: {e}")
            entry['media_files'] = []
            entry['ocr_text'] = ""
            return entry
    
    async def _download_media_file(self, message, download_dir, filename_prefix):
        """
        Download a single media file from Telegram message
        
        Args:
            message: Telegram message object
            download_dir: Directory to save to
            filename_prefix: Prefix for the filename
        
        Returns:
            str: Path to downloaded file or None
        """
        try:
            file_path = await self.telegram_client.client.download_media(
                message,
                file=download_dir
            )
            
            if file_path:
                logger.debug(f"Downloaded Telegram media: {file_path}")
                return file_path
            
            return None
            
        except Exception as e:
            logger.error(f"Error downloading Telegram media file: {e}")
            return None
    
    def cleanup_entry_media(self, entry):
        """
        Clean up temporary media files for an entry (only if older than 2 days)
        Media files are kept for 2 days to allow viewing in dashboard
        
        Args:
            entry: Entry dictionary with download_dir
        """
        download_dir = entry.get('download_dir')
        if download_dir and os.path.exists(download_dir):
            # Don't delete immediately - keep for 2 days
            # Cleanup will be handled by periodic cleanup task
            pass
    
    def run_download_telegram_media(self, entry):
        """
        Synchronous wrapper for download_telegram_media
        
        Args:
            entry: Telegram message entry
        
        Returns:
            dict: Updated entry
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(self.download_telegram_media(entry))

