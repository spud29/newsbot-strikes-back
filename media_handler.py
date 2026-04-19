"""
Media handler for downloading media from Twitter and Telegram
"""
import os
import sys
import json
import subprocess
import asyncio
from pathlib import Path
from utils import logger, retry_with_backoff, get_temp_dir, cleanup_temp_files, clean_text_content, resolve_shortened_urls, remove_emojis, remove_corrupted_emoji_marks, strip_wire_prefixes, format_quote_tweets, remove_twitter_attribution, remove_xcom_urls
import config
from ocr_handler import OCRHandler
from transcription_handler import TranscriptionHandler

class GalleryDlFailure(Exception):
    """Raised when gallery-dl fails to extract tweet content"""
    pass

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
        self.transcription_handler = TranscriptionHandler()
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
            
            # First, extract the full tweet text using gallery-dl --dump-json
            # Using --dump-json instead of --print ensures we get content even for
            # text-only tweets (--print only outputs when media items are found)
            logger.debug(f"Extracting tweet text using gallery-dl from: {link}")
            text_cmd = [
                sys.executable, '-m', 'gallery_dl',
                '--dump-json',
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

            # Extract text from gallery-dl JSON output
            full_text = ''
            if text_result.returncode == 0 and text_result.stdout.strip():
                try:
                    json_data = json.loads(text_result.stdout.strip())
                    # --dump-json returns a list of [directory_info, file_info, ...]
                    # The metadata dict contains the 'content' field
                    for item in json_data:
                        if isinstance(item, list) and len(item) >= 2 and isinstance(item[1], dict):
                            raw_output = item[1].get('content', '')
                            if raw_output:
                                full_text = raw_output.strip()
                                logger.debug(f"Raw gallery-dl output ({len(full_text)} chars): {full_text[:500]}")
                                logger.info(f"✓ Successfully extracted full text from x.com using gallery-dl ({len(full_text)} chars)")
                                break
                    if not full_text:
                        logger.warning(f"gallery-dl JSON had no content for: {link}")
                except json.JSONDecodeError:
                    # Fallback: try treating output as plain text (legacy --print behavior)
                    raw_output = text_result.stdout.strip()
                    logger.debug(f"gallery-dl output not JSON, using as plain text ({len(raw_output)} chars): {raw_output[:500]}")
                    full_text = raw_output
                    if full_text:
                        logger.info(f"✓ Successfully extracted full text from x.com using gallery-dl ({len(full_text)} chars)")
                    else:
                        logger.warning(f"gallery-dl returned empty content for: {link}")
            else:
                logger.warning(f"gallery-dl failed to extract text (return code: {text_result.returncode})")
                if text_result.stderr:
                    logger.debug(f"gallery-dl stderr: {text_result.stderr[:200]}")
            
            # If gallery-dl didn't get text, fall back to RSS content
            if not full_text:
                logger.info(f"gallery-dl returned no content, falling back to RSS feed content")
                full_text = entry.get('content', '')
                # Apply the same cleaning pipeline as gallery-dl content
                if full_text:
                    full_text = clean_text_content(full_text)
                    full_text = resolve_shortened_urls(full_text)
                    full_text = remove_emojis(full_text)
                    full_text = remove_corrupted_emoji_marks(full_text)
                    full_text = strip_wire_prefixes(full_text)
                    full_text = remove_xcom_urls(full_text)
                    full_text = remove_twitter_attribution(full_text)
                else:
                    # Only raise exception if both gallery-dl AND RSS have no content
                    logger.warning(f"No content available from gallery-dl or RSS for: {link}")
                    raise GalleryDlFailure(f"No content available from any source for {link}")
            
            # Extract video URLs using gallery-dl -g
            video_urls = []
            video_url_cmd = [
                sys.executable, '-m', 'gallery_dl',
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
            
            # Extract video duration if there are videos
            video_duration = None
            if video_urls:
                duration_cmd = [
                    sys.executable, '-m', 'gallery_dl',
                    '--print', '{duration}',
                    '--range', '1',
                    '--no-download',
                    link
                ]
                
                duration_result = subprocess.run(
                    duration_cmd,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=30
                )
                
                if duration_result.returncode == 0 and duration_result.stdout.strip():
                    try:
                        video_duration = float(duration_result.stdout.strip().split('\n')[0])
                        logger.info(f"Video duration: {video_duration:.1f} seconds")
                    except (ValueError, IndexError):
                        logger.debug(f"Could not parse video duration: {duration_result.stdout.strip()}")
            
            # Now download media files (images only, skip videos)
            # Videos are handled via video_urls to avoid Discord file size limits
            media_cmd = [
                sys.executable, '-m', 'gallery_dl',
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

            if media_result.returncode != 0:
                logger.warning(f"gallery-dl image download failed (return code: {media_result.returncode}) for {link}")
                if media_result.stderr:
                    logger.debug(f"gallery-dl image download stderr: {media_result.stderr[:500]}")
                raise GalleryDlFailure(f"gallery-dl image download failed for {link}")

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
            full_text = strip_wire_prefixes(full_text)
            full_text = remove_xcom_urls(full_text)
            full_text = format_quote_tweets(full_text)
            full_text = remove_twitter_attribution(full_text)
            
            # Extract text from images using OCR
            ocr_text = ""
            if media_files:
                logger.debug(f"Running OCR on {len(media_files)} Twitter images...")
                ocr_text = self.ocr_handler.extract_text_from_images(media_files)
                if ocr_text:
                    logger.info(f"✓ OCR extracted {len(ocr_text)} characters from Twitter images")

            # Transcribe audio from the first video URL (if any)
            audio_transcript = ""
            if video_urls:
                logger.debug(f"Transcribing audio from Twitter video URL...")
                audio_transcript = self.transcription_handler.transcribe_from_url(video_urls[0])

            logger.debug("Assigning entry values...")
            entry['media_files'] = media_files
            entry['video_urls'] = video_urls
            entry['video_duration'] = video_duration
            entry['full_text'] = full_text
            entry['ocr_text'] = ocr_text
            entry['audio_transcript'] = audio_transcript
            entry['download_dir'] = download_dir
            logger.debug("Entry values assigned successfully")
            
            logger.info(f"Downloaded {len(media_files)} image files from Twitter, extracted {len(video_urls)} video URLs")
            logger.debug("Returning entry from download_twitter_media")
            return entry
            
        except subprocess.TimeoutExpired:
            logger.error("gallery-dl timed out")
            entry['media_files'] = []
            entry['ocr_text'] = ""
            entry['audio_transcript'] = ""
            return entry
        except GalleryDlFailure:
            # Re-raise GalleryDlFailure so it can be handled by retry queue
            raise
        except Exception as e:
            logger.error(f"Error downloading Twitter media: {e}")
            entry['media_files'] = []
            entry['ocr_text'] = ""
            entry['audio_transcript'] = ""
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
            logger.debug(f"Entry has no media flag set, skipping download for: {entry['id']}")
            entry['media_files'] = []
            return entry
        
        logger.debug(
            f"Downloading Telegram media for: {entry['id']} "
            f"(media_type: {entry.get('media_type')}, is_album: {entry.get('is_album', False)})"
        )
        
        try:
            # Create temporary directory for this download
            download_dir = os.path.join(self.temp_dir, f"telegram_{entry['message_id']}")
            os.makedirs(download_dir, exist_ok=True)
            
            media_files = []
            video_urls = []
            
            # Check if this is an album
            if entry.get('is_album') and entry.get('album_messages'):
                # Download all media in the album
                logger.debug(f"Downloading album with {len(entry['album_messages'])} messages")
                for i, message in enumerate(entry['album_messages']):
                    if message.media:
                        logger.debug(f"Downloading album media {i+1}/{len(entry['album_messages'])}")
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
                            logger.warning(f"Failed to download album media {i+1}/{len(entry['album_messages'])}")
                    else:
                        logger.warning(f"Album message {i+1}/{len(entry['album_messages'])} has no media")
            else:
                # Single media file
                message = entry.get('message_obj')
                
                if not message:
                    logger.warning(f"No message_obj found in entry {entry['id']}, cannot download media")
                elif not message.media:
                    logger.warning(
                        f"message_obj exists but has no media attribute for entry {entry['id']}. "
                        f"Message type: {type(message)}, has_media flag: {entry.get('has_media')}"
                    )
                else:
                    logger.debug(f"Downloading single media file from message {entry.get('message_id')}")
                    file_path = await self._download_media_file(
                        message,
                        download_dir,
                        "media"
                    )
                    if file_path:
                        media_files.append(file_path)
                        logger.debug(f"Successfully downloaded: {file_path}")
                        
                        # Check if it's a video
                        ext = os.path.splitext(file_path)[1].lower()
                        if ext in ['.mp4', '.mov', '.avi']:
                            video_urls.append("telegram_video")
                    else:
                        logger.warning(f"Download returned None for entry {entry['id']}")
            
            # Extract text from images using OCR (skip videos)
            ocr_text = ""
            if media_files:
                # Filter to only image files for OCR
                image_files = [f for f in media_files if os.path.splitext(f)[1].lower() in ['.jpg', '.jpeg', '.png', '.gif', '.webp']]
                if image_files:
                    logger.debug(f"Running OCR on {len(image_files)} Telegram images...")
                    ocr_text = await asyncio.to_thread(self.ocr_handler.extract_text_from_images, image_files)
                    if ocr_text:
                        logger.info(f"✓ OCR extracted {len(ocr_text)} characters from Telegram images")

            # Transcribe audio from downloaded video files (if any)
            audio_transcript = ""
            video_files = [f for f in media_files if os.path.splitext(f)[1].lower() in ['.mp4', '.mov', '.avi', '.mkv', '.webm']]
            if video_files:
                logger.debug(f"Transcribing audio from {len(video_files)} Telegram video(s)...")
                audio_transcript = await asyncio.to_thread(
                    self.transcription_handler.transcribe_audio_file, video_files[0]
                )

            entry['media_files'] = media_files
            entry['video_urls'] = video_urls
            entry['ocr_text'] = ocr_text
            entry['audio_transcript'] = audio_transcript
            entry['download_dir'] = download_dir
            
            logger.info(f"Downloaded {len(media_files)} media files from Telegram")
            return entry
            
        except Exception as e:
            logger.error(f"Error downloading Telegram media: {e}")
            entry['media_files'] = []
            entry['ocr_text'] = ""
            entry['audio_transcript'] = ""
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
            logger.debug(f"Calling telegram_client.download_media() to {download_dir}")
            file_path = await self.telegram_client.client.download_media(
                message,
                file=download_dir
            )
            
            if file_path:
                file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
                logger.info(f"Downloaded Telegram media: {file_path} ({file_size} bytes)")
                return file_path
            else:
                logger.warning(
                    f"download_media() returned None/empty. "
                    f"Message ID: {message.id if hasattr(message, 'id') else 'unknown'}, "
                    f"Has media: {hasattr(message, 'media') and message.media is not None}"
                )
                return None
            
        except Exception as e:
            logger.error(
                f"Error downloading Telegram media file: {e} "
                f"(Message ID: {message.id if hasattr(message, 'id') else 'unknown'})",
                exc_info=True
            )
            return None
    
    async def redownload_media(self, entry_id, source_type, source_url, telegram_message_id):
        """
        Re-download media for an entry that was previously processed.
        Used by the 'Restore Original' command to re-attach images when restoring
        a superseded entry.

        Args:
            entry_id: Full entry ID (e.g., 'telegram_ChannelName_12345')
            source_type: 'telegram' or 'twitter'
            source_url: Original source URL (used for Twitter re-downloads)
            telegram_message_id: Telegram message ID integer (used for Telegram re-downloads)

        Returns:
            tuple: (media_files: list[str], video_urls: list[str])
        """
        try:
            if source_type == 'telegram' and telegram_message_id:
                return await self._redownload_telegram_media(entry_id, telegram_message_id)
            elif source_url and source_type in ('twitter', 'rss'):
                return await asyncio.to_thread(self._redownload_twitter_media, entry_id, source_url)
        except Exception as e:
            logger.error(f"redownload_media failed for {entry_id}: {e}", exc_info=True)
        return [], []

    async def _redownload_telegram_media(self, entry_id, telegram_message_id):
        """Re-fetch a Telegram message and download its media."""
        if not self.telegram_client or not self.telegram_client.client:
            logger.warning("Telegram client unavailable for media re-download")
            return [], []

        # Parse channel name: entry_id is "telegram_{channel}_{message_id}"
        without_prefix = entry_id[len('telegram_'):]
        channel_name = without_prefix.rsplit('_', 1)[0]

        client = self.telegram_client.client
        channel_entity = await client.get_entity(channel_name)

        # Fetch the original message
        message = await client.get_messages(channel_entity, ids=telegram_message_id)
        if not message or not message.media:
            logger.info(f"No media found on re-fetched Telegram message {telegram_message_id}")
            return [], []

        # For albums, fetch siblings that share the same grouped_id
        if message.grouped_id:
            id_range = list(range(telegram_message_id, telegram_message_id + 10))
            candidates = await client.get_messages(channel_entity, ids=id_range)
            album_msgs = sorted(
                [m for m in candidates if m and m.grouped_id == message.grouped_id],
                key=lambda m: m.id
            )
        else:
            album_msgs = [message]

        download_dir = os.path.join(self.temp_dir, f"telegram_{telegram_message_id}_restored")
        os.makedirs(download_dir, exist_ok=True)

        media_files = []
        video_urls = []
        for i, msg in enumerate(album_msgs):
            if not msg.media:
                continue
            file_path = await self._download_media_file(msg, download_dir, f"media_{i}")
            if file_path:
                media_files.append(file_path)
                if os.path.splitext(file_path)[1].lower() in ('.mp4', '.mov', '.avi'):
                    video_urls.append("telegram_video")

        logger.info(f"Re-downloaded {len(media_files)} file(s) for {entry_id}")
        return media_files, video_urls

    def _redownload_twitter_media(self, entry_id, source_url):
        """Re-download Twitter media via gallery-dl."""
        status_id = entry_id.split('_')[-1] if entry_id else 'unknown'
        entry = {'id': entry_id, 'link': source_url, 'status_id': status_id}
        updated = self.download_twitter_media(entry)
        return updated.get('media_files', []), updated.get('video_urls', [])

    def cleanup_entry_media(self, entry):
        """
        Clean up temporary media files for an entry (only if older than 2 days)
        Media files are kept for 2 days before cleanup
        
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

