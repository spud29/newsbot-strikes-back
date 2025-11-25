"""
Main entry point for Discord News Aggregator Bot
"""
import asyncio
import time
import signal
import sys
import subprocess
import os
from utils import logger, setup_logging
import config
from database import Database
from ollama_client import OllamaClient
from rss_poller import RSSPoller
from telegram_poller import TelegramPoller
from media_handler import MediaHandler, GalleryDlFailure
from discord_poster import DiscordPoster
from retry_queue import RetryQueue
from perplexity_client import PerplexityClient
from vote_tracker import VoteTracker
from removed_entries import RemovedEntriesDB

class NewsAggregatorBot:
    """Main bot orchestrator"""
    
    def __init__(self):
        """Initialize all components"""
        logger.info("=" * 80)
        logger.info("Initializing Discord News Aggregator Bot")
        logger.info("=" * 80)
        
        self.db = Database()
        self.vote_tracker = VoteTracker()
        self.removed_entries_db = RemovedEntriesDB()
        self.ollama = OllamaClient(removed_entries_db=self.removed_entries_db)
        self.perplexity = PerplexityClient()
        self.rss_poller = RSSPoller()
        self.telegram_poller = TelegramPoller()
        self.media_handler = MediaHandler(telegram_client=self.telegram_poller)
        self.discord_poster = DiscordPoster(
            perplexity_client=self.perplexity,
            database=self.db,
            vote_tracker=self.vote_tracker,
            removed_entries_db=self.removed_entries_db
        )
        self.retry_queue = RetryQueue(max_retries=3, retry_delay_cycles=2)
        
        self.running = False
        
        # Statistics
        self.stats = {
            'processed': 0,
            'duplicates': 0,
            'errors': 0,
            'by_category': {}
        }
        
        logger.info("All components initialized")
    
    async def start(self):
        """Start the bot"""
        logger.info("Starting bot...")
        
        # Write PID file for dashboard management
        pid_file = os.path.join("data", "bot.pid")
        try:
            os.makedirs("data", exist_ok=True)
            with open(pid_file, "w") as f:
                f.write(str(os.getpid()))
            logger.info(f"PID file created: {pid_file} (PID: {os.getpid()})")
        except Exception as e:
            logger.error(f"Failed to write PID file: {e}")
        
        # Health check Ollama
        if not self.ollama.health_check():
            logger.error("Ollama health check failed! Please ensure Ollama is running.")
            return
        
        # Start Discord client
        await self.discord_poster.start()
        
        # Start Telegram client
        await self.telegram_poller.start()
        
        self.running = True
        logger.info("Bot started successfully")
    
    async def stop(self):
        """Stop the bot"""
        logger.info("Stopping bot...")
        self.running = False
        
        # Stop Telegram and Discord clients
        await self.telegram_poller.stop()
        await self.discord_poster.stop()
        
        # Clean up PID file
        pid_file = os.path.join("data", "bot.pid")
        try:
            if os.path.exists(pid_file):
                os.remove(pid_file)
                logger.info("PID file cleaned up")
        except Exception as e:
            logger.error(f"Failed to remove PID file: {e}")
        
        logger.info("Bot stopped")
    
    async def process_entry(self, entry):
        """
        Process a single entry through the full pipeline
        
        Args:
            entry: Entry dictionary from RSS or Telegram
        
        Returns:
            bool: True if successfully processed and posted
        """
        try:
            entry_id = entry['id']
            source_type = entry['source_type']
            
            logger.info(f"\nProcessing entry: {entry_id}")
            logger.debug(f"Source: {entry['source']} ({source_type})")
            
            # Check if already processed
            if self.db.is_processed(entry_id):
                logger.debug(f"Already processed, skipping: {entry_id}")
                return False
            
            # Get initial content for duplicate check (before downloading media)
            content = entry.get('content', '')
            
            # Special handling for image-only Telegram entries
            # Download media and extract OCR text BEFORE content validation
            if source_type == 'telegram' and not content and entry.get('has_media'):
                logger.info(f"Image-only Telegram entry detected, downloading media for OCR...")
                entry = await self.media_handler.download_telegram_media(entry)
                media_files = entry.get('media_files', [])
                ocr_text = entry.get('ocr_text', '')
                
                # BUGFIX: If this is an image-only entry but download failed, skip it entirely
                if not media_files:
                    logger.error(
                        f"Image-only Telegram entry has no media files after download! "
                        f"Entry ID: {entry_id}, has_media: {entry.get('has_media')}, "
                        f"media_type: {entry.get('media_type')}. Skipping this entry."
                    )
                    self.stats['errors'] += 1
                    return False
                
                if ocr_text:
                    # Use OCR text as the content for image-only entries
                    content = ocr_text
                    entry['content'] = content
                    logger.info(f"Using OCR text as content ({len(content)} chars)")
                else:
                    # If OCR extraction failed or is disabled, use a placeholder
                    # This allows image-only posts to still be processed
                    content = "[Image content - no text extracted]"
                    entry['content'] = content
                    logger.info(f"No OCR text available, using placeholder content for image-only entry with {len(media_files)} media file(s)")
            
            if not content:
                logger.warning(f"No content to process for: {entry_id}")
                self.stats['errors'] += 1
                return False
            
            # Generate embedding for duplicate detection BEFORE downloading media
            # Note: OCR text will be added after media download for enhanced detection
            logger.debug("Generating embedding for duplicate check...")
            embedding = self.ollama.generate_embedding(content)
            
            # Check for exact duplicates BEFORE downloading media
            is_duplicate, duplicate_similarity, match_preview = self.db.find_similar(
                embedding, 
                threshold=config.DUPLICATE_THRESHOLD
            )
            
            if is_duplicate:
                # True duplicate (>0.95 similarity) - skip entirely
                logger.info(
                    f"Exact duplicate detected (similarity: {duplicate_similarity:.3f}): {entry_id}\n"
                    f"Matches: {match_preview}"
                )
                self.stats['duplicates'] += 1
                return False
            
            # Check for similar content (not exact duplicate)
            is_similar, similar_similarity, similar_preview = self.db.find_similar(
                embedding,
                threshold=config.SIMILARITY_THRESHOLD
            )
            
            force_category = None
            if is_similar and not is_duplicate:
                # Similar but not duplicate - force to ignore category
                logger.info(
                    f"Similar content detected (similarity: {similar_similarity:.3f}): {entry_id}\n"
                    f"Matches: {similar_preview}\n"
                    f"Routing to ignore channel"
                )
                force_category = 'ignore'
            
            # Download media (for both new and similar entries)
            # Skip if already downloaded (e.g., for image-only Telegram entries)
            if not entry.get('media_files'):
                logger.debug("Not a duplicate, downloading media...")
                if source_type == 'twitter':
                    entry = self.media_handler.download_twitter_media(entry)
                    # Update content with gallery-dl extracted text if available
                    content = entry.get('full_text') or content
                elif source_type == 'telegram':
                    entry = await self.media_handler.download_telegram_media(entry)
                    # Content should already be set, but update if needed
                    content = entry.get('content', content)
            else:
                logger.debug("Media already downloaded, skipping download step...")
            
            # Combine OCR text with content for better categorization
            ocr_text = entry.get('ocr_text', '')
            combined_content = content
            if ocr_text:
                combined_content = f"{content}\n\n[Text from images]:\n{ocr_text}"
                logger.debug(f"Combined content with OCR text ({len(ocr_text)} chars from images)")
            
            # Categorize content (use forced category if similar content)
            if force_category:
                category = force_category
                logger.info(f"Category: {category} (forced due to similarity)")
            else:
                logger.debug("Categorizing content...")
                category = self.ollama.categorize(combined_content)
                logger.info(f"Category: {category}")
            
            # Post to Discord
            media_files = entry.get('media_files', [])
            video_urls = entry.get('video_urls', [])
            
            logger.debug(f"Posting to Discord: {len(media_files)} files, {len(video_urls)} videos")
            
            success, discord_message_id, discord_channel_id = await self.discord_poster.post_message(
                category=category,
                content=content,
                media_files=media_files,
                video_urls=video_urls,
                source_type=source_type,
                entry_id=entry_id
            )
            
            if success:
                # Mark as processed and store embedding
                # Use combined content with OCR text for better duplicate detection in future
                self.db.mark_processed(entry_id)
                if ocr_text:
                    # Store embedding with OCR text included for better future duplicate detection
                    combined_embedding = self.ollama.generate_embedding(combined_content)
                    self.db.add_embedding(combined_content, combined_embedding, entry_id=entry_id)
                else:
                    self.db.add_embedding(content, embedding, entry_id=entry_id)
                
                # Store message mapping with source URL for all entries
                if discord_message_id and discord_channel_id:
                    # Get or construct source URL
                    source_url = entry.get('link') or entry.get('url')
                    
                    # For Telegram entries, construct the t.me URL
                    if source_type == 'telegram' and not source_url:
                        message_id = entry.get('message_id')
                        channel_name = entry.get('source')
                        if message_id and channel_name:
                            # Remove @ prefix if present (t.me URLs don't use it)
                            channel_name_clean = channel_name.lstrip('@')
                            source_url = f"https://t.me/{channel_name_clean}/{message_id}"
                    
                    self.db.store_message_mapping(
                        telegram_entry_id=entry_id,
                        telegram_message_id=entry.get('message_id', 0),
                        discord_channel_id=discord_channel_id,
                        discord_message_id=discord_message_id,
                        content=content,
                        source_url=source_url,
                        video_urls=entry.get('video_urls', []),
                        category=category
                    )
                
                # Update last message ID for Telegram entries
                if source_type == 'telegram':
                    message_id = entry.get('message_id')
                    if message_id:
                        self.telegram_poller.update_last_message_id(entry_id, message_id)
                
                # Update statistics
                self.stats['processed'] += 1
                self.stats['by_category'][category] = self.stats['by_category'].get(category, 0) + 1
                
                logger.info(f"✓ Successfully processed and posted: {entry_id}")
            else:
                logger.error(f"Failed to post to Discord: {entry_id}")
                self.stats['errors'] += 1
            
            # Clean up temporary media files
            self.media_handler.cleanup_entry_media(entry)
            
            return success
            
        except GalleryDlFailure as e:
            # gallery-dl failed to extract content - add to retry queue
            logger.warning(f"gallery-dl failure for {entry.get('id', 'unknown')}: {e}")
            self.retry_queue.add_entry(entry)
            self.stats['errors'] += 1
            
            # Clean up on error
            try:
                self.media_handler.cleanup_entry_media(entry)
            except:
                pass
            
            return False
            
        except Exception as e:
            logger.error(f"Error processing entry {entry.get('id', 'unknown')}: {e}", exc_info=True)
            self.stats['errors'] += 1
            
            # Clean up on error
            try:
                self.media_handler.cleanup_entry_media(entry)
            except:
                pass
            
            return False
    
    async def poll_cycle(self):
        """Run one polling cycle"""
        logger.info("\n" + "=" * 80)
        logger.info(f"Starting polling cycle at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80)
        
        cycle_start = time.time()
        
        # Increment retry queue cycle counter
        self.retry_queue.increment_cycle()
        
        # Reset per-cycle statistics
        self.stats['duplicates'] = 0
        self.stats['errors'] = 0
        self.stats['processed'] = 0
        self.stats['by_category'] = {}
        
        # Clean up old database entries
        logger.info("Cleaning up old database entries...")
        self.db.cleanup_old_entries()
        
        # Clean up old retry queue entries (older than 24 hours)
        self.retry_queue.cleanup_old_entries(max_age_hours=24)
        
        # Clean up old media files (older than 2 days)
        from utils import cleanup_old_media_files
        cleanup_old_media_files(retention_days=2)
        
        # Get database stats
        db_stats = self.db.get_stats()
        retry_stats = self.retry_queue.get_stats()
        logger.info(f"Database: {db_stats['processed_ids']} IDs, {db_stats['embeddings']} embeddings")
        if retry_stats['total_entries'] > 0:
            logger.info(f"Retry Queue: {retry_stats['total_entries']} entries waiting for retry")
        
        # Collect entries from all sources
        all_entries = []
        
        # Get entries from retry queue first (priority)
        retry_entries = self.retry_queue.get_entries_to_retry()
        if retry_entries:
            logger.info(f"\n--- Processing retry queue ({len(retry_entries)} entries) ---")
            all_entries.extend(retry_entries)
        
        # Poll RSS feeds
        try:
            logger.info("\n--- Polling RSS feeds ---")
            rss_entries = self.rss_poller.poll_all_feeds()
            all_entries.extend(rss_entries)
        except Exception as e:
            logger.error(f"Error polling RSS feeds: {e}")
        
        # Poll Telegram channels
        try:
            logger.info("\n--- Polling Telegram channels ---")
            telegram_entries = await self.telegram_poller.poll_all_channels()
            all_entries.extend(telegram_entries)
        except Exception as e:
            logger.error(f"Error polling Telegram channels: {e}")
        
        logger.info(f"\nTotal entries collected: {len(all_entries)} ({len(retry_entries)} from retry queue)")
        
        # Sort entries by timestamp in reverse chronological order (newest first)
        def get_entry_timestamp(entry):
            """Extract timestamp from entry for sorting"""
            if 'timestamp' in entry and entry['timestamp']:
                return entry['timestamp']
            elif 'pub_date' in entry and entry['pub_date']:
                # Parse RSS pub_date to timestamp
                import time
                from email.utils import parsedate_to_datetime
                try:
                    dt = parsedate_to_datetime(entry['pub_date'])
                    return dt.timestamp()
                except:
                    return 0
            return 0
        
        all_entries.sort(key=get_entry_timestamp, reverse=False)
        logger.info("Entries sorted by timestamp (oldest first)")
        
        # Track already seen entries
        already_seen = 0
        
        # Process each entry sequentially
        if all_entries:
            logger.info("\n--- Processing entries ---")
            
            for i, entry in enumerate(all_entries, 1):
                logger.info(f"\nEntry {i}/{len(all_entries)}")
                
                # Check if already processed before doing expensive operations
                if self.db.is_processed(entry['id']):
                    already_seen += 1
                    logger.debug(f"Already processed, skipping: {entry['id']}")
                    # Remove from retry queue if it was a retry
                    self.retry_queue.remove_entry(entry['id'], reason="already_processed")
                    continue
                
                success = await self.process_entry(entry)
                
                # If successful and was in retry queue, remove it
                if success:
                    self.retry_queue.remove_entry(entry['id'], reason="success")
        
        cycle_duration = time.time() - cycle_start
        
        # Log cycle summary
        logger.info("\n" + "=" * 80)
        logger.info("Cycle Summary:")
        logger.info(f"  Duration: {cycle_duration:.2f}s")
        logger.info(f"  Entries collected: {len(all_entries)}")
        logger.info(f"  Already seen: {already_seen}")
        logger.info(f"  Duplicates detected: {self.stats['duplicates']}")
        logger.info(f"  Successfully processed: {self.stats['processed']}")
        logger.info(f"  Errors: {self.stats['errors']}")
        
        if self.stats['by_category']:
            logger.info("\n  By Category:")
            for category, count in sorted(self.stats['by_category'].items()):
                logger.info(f"    {category}: {count}")
        
        logger.info("=" * 80)
    
    async def process_telegram_queue(self):
        """Process messages from the Telegram real-time queue"""
        while True:
            try:
                entry = await self.telegram_poller.get_queued_message()
                if entry:
                    logger.info(f"Processing real-time Telegram message: {entry['id']}")
                    await self.process_entry(entry)
                else:
                    # No message available, brief sleep
                    await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Error processing Telegram queue: {e}", exc_info=True)
                await asyncio.sleep(1)
    
    async def process_telegram_edits(self):
        """Process edited messages from the Telegram edit queue"""
        while True:
            try:
                edited_entry = await self.telegram_poller.get_queued_edit()
                if edited_entry:
                    logger.info(f"Processing edited Telegram message: {edited_entry['id']}")
                    await self.process_telegram_edit(edited_entry)
                else:
                    # No edit available, brief sleep
                    await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Error processing Telegram edit queue: {e}", exc_info=True)
                await asyncio.sleep(1)
    
    async def process_telegram_edit(self, edited_entry):
        """
        Process an edited Telegram message and update the corresponding Discord message
        
        Args:
            edited_entry: Edited entry dictionary from Telegram
        
        Returns:
            bool: True if successfully processed and updated
        """
        try:
            entry_id = edited_entry['id']
            
            logger.info(f"\nProcessing edited message: {entry_id}")
            
            # Check if we have a mapping for this Telegram message
            mapping_info = self.db.get_discord_message_info(entry_id)
            
            if not mapping_info:
                logger.warning(f"No Discord mapping found for edited Telegram message: {entry_id}")
                return False
            
            discord_channel_id = mapping_info['discord_channel_id']
            discord_message_id = mapping_info['discord_message_id']
            old_content = mapping_info.get('content', '')
            
            logger.info(f"Found Discord message mapping: Channel {discord_channel_id}, Message {discord_message_id}")
            
            # Get the edited content
            new_content = edited_entry.get('content', '')
            
            if not new_content:
                logger.warning(f"No content in edited message: {entry_id}")
                return False
            
            # Compare old and new content
            if old_content == new_content:
                logger.info(f"Content unchanged, skipping Discord update for: {entry_id}")
                return True  # Not an error, just no action needed
            
            logger.info(f"Content changed, updating Discord message...")
            logger.debug(f"Old content: {old_content[:100]}...")
            logger.debug(f"New content: {new_content[:100]}...")
            
            # Update the Discord message
            success = await self.discord_poster.edit_message(
                channel_id=discord_channel_id,
                message_id=discord_message_id,
                content=new_content,
                source_type='telegram'
            )
            
            if success:
                # Update the stored content in the mapping (preserve source_url and category)
                self.db.store_message_mapping(
                    telegram_entry_id=entry_id,
                    telegram_message_id=mapping_info['telegram_message_id'],
                    discord_channel_id=discord_channel_id,
                    discord_message_id=discord_message_id,
                    content=new_content,
                    source_url=mapping_info.get('source_url'),
                    video_urls=mapping_info.get('video_urls', []),
                    category=mapping_info.get('category')
                )
                logger.info(f"✓ Successfully updated Discord message for edited Telegram message: {entry_id}")
            else:
                logger.error(f"Failed to update Discord message for: {entry_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error processing edited message {edited_entry.get('id', 'unknown')}: {e}", exc_info=True)
            return False
    
    async def run(self):
        """Main run loop"""
        await self.start()
        
        logger.info(f"\nStarting main loop (polling every {config.POLL_INTERVAL}s)...")
        
        # Start Telegram queue processors as background tasks
        telegram_queue_task = asyncio.create_task(self.process_telegram_queue())
        telegram_edit_task = asyncio.create_task(self.process_telegram_edits())
        
        try:
            while self.running:
                try:
                    await self.poll_cycle()
                    
                    # Wait for next cycle
                    logger.info(f"\nWaiting {config.POLL_INTERVAL}s until next cycle...\n")
                    await asyncio.sleep(config.POLL_INTERVAL)
                    
                except KeyboardInterrupt:
                    logger.info("\nReceived interrupt signal")
                    break
                except Exception as e:
                    logger.error(f"Error in main loop: {e}", exc_info=True)
                    # Wait a bit before retrying
                    await asyncio.sleep(30)
        finally:
            # Cancel the Telegram queue tasks
            telegram_queue_task.cancel()
            telegram_edit_task.cancel()
            try:
                await telegram_queue_task
            except asyncio.CancelledError:
                pass
            try:
                await telegram_edit_task
            except asyncio.CancelledError:
                pass
            
            await self.stop()

def signal_handler(sig, frame):
    """Handle shutdown signals"""
    logger.info("\nShutdown signal received")
    sys.exit(0)

async def main():
    """Main entry point"""
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Create and run bot
    bot = NewsAggregatorBot()
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nBot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

