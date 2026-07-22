"""
Main entry point for Discord News Aggregator Bot
"""
import asyncio
import time
import signal
import sys
import subprocess
import os
from collections import deque
from utils import logger, setup_logging, is_all_caps, strip_wire_prefixes, is_audience_question, cleanup_bot_log, shorten_dexerto_url_in_text
import config
from db_connection import close_db_connection
from database import Database
from ollama_client import OllamaClient
from rss_poller import RSSPoller
from telegram_poller import TelegramPoller
from media_handler import MediaHandler, GalleryDlFailure
from discord_poster import DiscordPoster
from retry_queue import RetryQueue
from perplexity_client import PerplexityClient
from removed_entries import RemovedEntriesDB
from ocr_handler import is_tradingview_chart_ocr
from dexerto_merger import DexertoMerger

class NewsAggregatorBot:
    """Main bot orchestrator"""
    
    def __init__(self):
        """Initialize all components"""
        logger.info("=" * 80)
        logger.info("Initializing Discord News Aggregator Bot")
        logger.info("=" * 80)
        
        self.db = Database()
        self.removed_entries_db = RemovedEntriesDB()
        self.ollama = OllamaClient(removed_entries_db=self.removed_entries_db, database=self.db)
        self.perplexity = PerplexityClient()
        self.rss_poller = RSSPoller()
        self.telegram_poller = TelegramPoller()
        self.media_handler = MediaHandler(telegram_client=self.telegram_poller)
        self.discord_poster = DiscordPoster(
            perplexity_client=self.perplexity,
            database=self.db,
            removed_entries_db=self.removed_entries_db,
            ollama=self.ollama
        )
        self.retry_queue = RetryQueue(max_retries=3, retry_delay_cycles=2)
        self.dexerto_merger = DexertoMerger(
            db=self.db,
            process_entry_fn=self.process_entry,
            max_pending_hours=getattr(config, 'DEXERTO_PENDING_MAX_AGE_HOURS', 4.0)
        )

        self.running = False
        self._processing_lock = set()  # Track entries currently being processed (race condition guard)
        self._recent_post_times = deque()  # Timestamps of non-ignore posts (flood guard)
        self._last_log_cleanup: float = 0.0
        self._last_nightly_rebuild: float = 0.0
        # Persisted across restarts so a weekly report isn't re-posted every restart
        self._accuracy_report_state_file = os.path.join("data", "accuracy_report_state.txt")
        self._last_accuracy_report: float = self._read_accuracy_report_state()
        
        # Statistics
        self.stats = {
            'processed': 0,
            'duplicates': 0,
            'errors': 0,
            'by_category': {}
        }
        
        logger.info("All components initialized")

    def _read_accuracy_report_state(self) -> float:
        try:
            with open(self._accuracy_report_state_file) as f:
                return float(f.read().strip())
        except (OSError, ValueError):
            return 0.0

    def _write_accuracy_report_state(self, ts: float):
        try:
            with open(self._accuracy_report_state_file, "w") as f:
                f.write(str(ts))
        except OSError as e:
            logger.warning(f"Could not persist accuracy report state: {e}")

    async def start(self):
        """Start the bot"""
        logger.info("Starting bot...")
        
        # Write PID file
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
            
            # Check if already being processed by another path (race condition guard)
            # Prevents double-posting when real-time handler and polling cycle
            # pick up the same Telegram message simultaneously
            if entry_id in self._processing_lock:
                logger.debug(f"Already being processed by another path, skipping: {entry_id}")
                return False
            
            # Claim this entry before doing any expensive work
            self._processing_lock.add(entry_id)
            
            try:
                # Check if already processed
                if self.db.is_processed(entry_id):
                    logger.debug(f"Already processed, skipping: {entry_id}")
                    return False
                
                # Check if previously removed via voting
                if self.removed_entries_db.is_removed(entry_id):
                    logger.debug(f"Entry was previously removed by voting, skipping: {entry_id}")
                    return False
                
                # Get initial content for duplicate check (before downloading media)
                content = entry.get('content', '')
                content_from_ocr = False  # True when `content` is OCR/placeholder text, not a real caption
                
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
                        # Additional check for TradingView chart garbage
                        # (should be caught in OCR extraction, but double-check here)
                        if is_tradingview_chart_ocr(ocr_text):
                            logger.warning(
                                f"Skipping image-only entry with TradingView chart OCR garbage: {entry_id}"
                            )
                            self.stats['duplicates'] += 1  # Count as filtered/skipped
                            return False
                        
                        # Use OCR text as the content for image-only entries
                        content = ocr_text
                        entry['content'] = content
                        content_from_ocr = True  # OCR text drives categorization/dedup only, not display
                        logger.info(f"Using OCR text as content ({len(content)} chars)")
                    else:
                        # If OCR extraction failed or is disabled, use a placeholder
                        # This allows image-only posts to still be processed
                        content = "[Image content - no text extracted]"
                        entry['content'] = content
                        content_from_ocr = True  # placeholder is for processing only, not display
                        logger.info(f"No OCR text available, using placeholder content for image-only entry with {len(media_files)} media file(s)")
                
                if not content:
                    logger.warning(f"No content to process for: {entry_id}")
                    self.stats['errors'] += 1
                    return False

                # Generate embedding for duplicate detection BEFORE downloading media
                # Note: OCR text will be added after media download for enhanced detection
                # Wrapped in asyncio.to_thread() to prevent blocking Discord interactions
                logger.debug("Generating embedding for duplicate check...")
                embedding = await asyncio.to_thread(self.ollama.generate_embedding, content)

                # Single-pass similarity scan: check both duplicate and similarity thresholds at once
                best_similarity, match_preview, match_content, match_entry_id = self.db.find_best_match(embedding)

                # Classify the best match against both thresholds.
                # IMPORTANT: if the matched entry was itself sent to 'ignore' (e.g. during
                # PAUSE_MODE), it was never actually published — don't treat the new entry
                # as a duplicate of something the user never saw.
                matched_was_ignored = False
                if match_entry_id:
                    matched_mapping = self.db.get_discord_message_info(match_entry_id)
                    if matched_mapping and matched_mapping.get('category') == 'ignore':
                        matched_was_ignored = True
                        logger.info(
                            f"Matched entry {match_entry_id} was posted to ignore "
                            f"(similarity: {best_similarity:.3f}) — not suppressing new entry"
                        )

                duplicate_info = None
                if best_similarity >= config.DUPLICATE_THRESHOLD and not matched_was_ignored:
                    logger.info(
                        f"Exact duplicate detected (similarity: {best_similarity:.3f}): {entry_id}\n"
                        f"Matches: {match_preview}"
                    )
                    self.stats['duplicates'] += 1
                    duplicate_info = {
                        'similarity': best_similarity,
                        'match_preview': match_preview
                    }

                similarity_info = None
                if not duplicate_info and best_similarity >= config.SIMILARITY_THRESHOLD:
                    # Get top-N candidates above threshold — the single best match is not
                    # always the actual duplicate (a topically-related story can outscore it).
                    top_matches = self.db.find_top_matches(
                        embedding,
                        threshold=config.SIMILARITY_THRESHOLD,
                        limit=3
                    )
                    for cand_sim, cand_preview, cand_content, cand_entry_id in top_matches:
                        # Skip candidates that were themselves routed to ignore —
                        # they were never published, so don't suppress new entries against them.
                        if cand_entry_id:
                            cand_mapping = self.db.get_discord_message_info(cand_entry_id)
                            if cand_mapping and cand_mapping.get('category') == 'ignore':
                                logger.info(
                                    f"Skipping similarity candidate {cand_entry_id} "
                                    f"(was posted to ignore, score: {cand_sim:.3f})"
                                )
                                continue

                        logger.info(
                            f"Verifying similarity candidate (score: {cand_sim:.3f}): "
                            f"{cand_preview[:80]}"
                        )
                        is_truly_similar = await asyncio.to_thread(
                            self.ollama.verify_similarity, content, cand_content
                        )
                        if is_truly_similar:
                            similarity_info = {
                                'similarity': cand_sim,
                                'match_preview': cand_preview
                            }
                            # Update match vars so supersede logic targets the confirmed match
                            match_entry_id = cand_entry_id
                            match_content = cand_content
                            match_preview = cand_preview
                            logger.info(
                                f"Similar content CONFIRMED by LLM (similarity: {cand_sim:.3f}): {entry_id}\n"
                                f"Matches: {cand_preview}"
                            )
                            break
                        else:
                            logger.info(
                                f"LLM says DIFFERENT story (score: {cand_sim:.3f}) vs "
                                f"{cand_entry_id}: {cand_preview[:80]}"
                            )
                
                # Download media (for both new and similar entries)
                # Skip if already downloaded (e.g., for image-only Telegram entries)
                if not entry.get('media_files'):
                    logger.debug("Not a duplicate, downloading media...")
                    if source_type == 'twitter':
                        entry = await asyncio.to_thread(self.media_handler.download_twitter_media, entry)
                        # Update content with gallery-dl extracted text if available
                        content = entry.get('full_text') or content
                        # Dexerto merger: append tweet 2 follow-up text (blurb + article URL)
                        if entry.get('dexerto_follow_up'):
                            follow_up = await asyncio.to_thread(
                                shorten_dexerto_url_in_text, entry['dexerto_follow_up']
                            )
                            content = f"{content.rstrip()}\n{follow_up}"
                            logger.debug(f"DexertoMerger: appended follow-up text to content for {entry_id}")
                    elif source_type == 'telegram':
                        entry = await self.media_handler.download_telegram_media(entry)
                        # Content should already be set, but update if needed
                        content = entry.get('content', content)
                else:
                    logger.debug("Media already downloaded, skipping download step...")

                # Fix ALL CAPS content (wire-service style headlines from any source)
                # Runs after gallery-dl so the final tweet text is what gets checked and rewritten.
                # Must be after line above — gallery-dl full_text can restore all-caps if checked earlier.
                if getattr(config, 'CAPS_FIX_ENABLED', False):
                    if is_all_caps(content, threshold=getattr(config, 'CAPS_FIX_THRESHOLD', 0.65)):
                        logger.info(f"ALL CAPS detected in {source_type} entry, running text cleanup...")
                        original_content = content
                        content = await asyncio.to_thread(self.ollama.format_text, content)
                        entry['content'] = content
                        if content != original_content:
                            logger.info(f"Text formatted for {entry_id}")
                        else:
                            logger.debug(f"Text unchanged after format_text (returned original)")

                
                # Combine OCR text and audio transcript with content for better categorization
                ocr_text = entry.get('ocr_text', '')
                audio_transcript = entry.get('audio_transcript', '')
                combined_content = content
                if ocr_text:
                    combined_content = f"{combined_content}\n\n[Text from images]:\n{ocr_text}"
                    logger.debug(f"Combined content with OCR text ({len(ocr_text)} chars from images)")
                if audio_transcript:
                    combined_content = f"{combined_content}\n\n[Video transcript]:\n{audio_transcript}"
                    logger.debug(f"Combined content with audio transcript ({len(audio_transcript)} chars)")
                
                # Always run AI categorization to get reasoning
                # NOTE: Ollama calls are wrapped in asyncio.to_thread() to prevent blocking the event loop
                # This allows Discord interactions to be processed while Ollama is thinking
                logger.debug("Categorizing content...")
                category, reasoning, secondary_category = await asyncio.to_thread(self.ollama.categorize, combined_content)
                logger.info(f"Category: {category}" + (f" (secondary: {secondary_category})" if secondary_category else ""))
                _ai_category = category  # Capture before any overrides
                _ai_secondary_category = secondary_category
                placement_reason = None  # Will be set at each override point

                # If Ollama timed out or errored during categorization, the category
                # defaults to 'ignore' with an error reasoning. Don't post it — skip
                # this entry so it gets retried on the next poll cycle.
                if category == 'ignore' and reasoning and reasoning.startswith('Error during categorization'):
                    logger.warning(
                        f"Skipping {entry_id} — Ollama categorization failed, will retry next cycle: {reasoning}"
                    )
                    return False
                
                # NOTE: pause mode / auto-post gating now happens AFTER the content
                # filters (see "Routing decision" below) so the reaction gate runs
                # and its scores are recorded even while the bot is in review mode.

                # If exact duplicate was detected, override category to ignore
                # but preserve the AI reasoning and append duplicate info
                if duplicate_info:
                    ai_category = category
                    category = 'ignore'
                    secondary_category = None
                    reasoning = (
                        f"AI suggested '{ai_category}': {reasoning or 'no reasoning provided'} "
                        f"| OVERRIDDEN by duplicate detector "
                        f"(score: {duplicate_info['similarity']:.3f}, "
                        f"matches: {duplicate_info['match_preview'][:100]})"
                    )
                    logger.info(
                        f"Category overridden to 'ignore' due to duplicate "
                        f"(AI suggested: {ai_category})"
                    )
                    placement_reason = (
                        f"Duplicate override: exact duplicate detected "
                        f"(score: {duplicate_info['similarity']:.3f}), "
                        f"AI had suggested '{_ai_category}', routed to ignore"
                    )

                # Category sanity check: if the AI assigned a real category to the new
                # entry but it differs from the matched entry's stored category, this is
                # almost certainly a false positive from the embedding model (e.g. a short
                # crypto headline matching a sports headline at high similarity). Clear the
                # similarity match and let the entry post normally.
                if similarity_info and category != 'ignore' and match_entry_id:
                    matched_cat_mapping = self.db.get_discord_message_info(match_entry_id)
                    if matched_cat_mapping:
                        old_cat = matched_cat_mapping.get('category')
                        if old_cat and old_cat != 'ignore' and old_cat != category:
                            if similarity_info['similarity'] < 0.80:
                                logger.info(
                                    f"Category mismatch at low confidence "
                                    f"({similarity_info['similarity']:.3f}, {category} vs {old_cat}) "
                                    f"— treating as false positive, posting normally"
                                )
                                similarity_info = None
                            else:
                                logger.info(
                                    f"Category mismatch at high confidence: new entry is '{category}' "
                                    f"but matched entry {match_entry_id} is '{old_cat}' "
                                    f"(score: {similarity_info['similarity']:.3f}) — letting supersede logic decide"
                                )

                # If similar content was detected, try to supersede the old entry
                # or fall back to routing to ignore
                superseded = False
                keep_both = False
                if similarity_info:
                    if getattr(config, 'SUPERSEDE_ENABLED', False) and match_entry_id:
                        old_mapping = self.db.get_discord_message_info(match_entry_id)
                        max_age = getattr(config, 'SUPERSEDE_MAX_AGE_HOURS', 24) * 3600
                        old_is_fresh = (
                            old_mapping
                            and old_mapping.get('category') != 'ignore'
                            and old_mapping.get('timestamp')
                            and (time.time() - old_mapping['timestamp']) < max_age
                        )
                        supersede_threshold = getattr(config, 'SUPERSEDE_SIMILARITY_THRESHOLD', 0.85)
                        if old_is_fresh:
                            if similarity_info['similarity'] >= supersede_threshold:
                                comparison = await asyncio.to_thread(
                                    self.ollama.compare_entries, content, match_content
                                )
                            else:
                                comparison = {'verdict': 'keep_both', 'reasoning': 'Similarity below supersede threshold'}
                            if comparison['verdict'] == 'keep_both':
                                keep_both = True
                                logger.info(
                                    f"Keeping both entries — unique info in each: "
                                    f"{comparison['reasoning']}"
                                )
                            elif comparison['verdict'] == 'supersede':
                                # Use the old entry's category so it posts to the same channel
                                category = old_mapping['category']
                                reasoning = (
                                    f"AI suggested '{category}': {reasoning or 'no reasoning provided'} "
                                    f"| SUPERSEDES {match_entry_id} "
                                    f"(similarity: {similarity_info['similarity']:.3f}, "
                                    f"reason: {comparison['reasoning']})"
                                )
                                # Delete old Discord message — only proceed if deletion succeeds
                                deleted = await self.discord_poster.delete_message(
                                    old_mapping['discord_channel_id'],
                                    old_mapping['discord_message_id']
                                )
                                if deleted:
                                    # Archive superseded entry to dedicated channel, clean up embedding
                                    archived, archived_msg_id = await self.discord_poster.post_superseded_entry(
                                        old_mapping, content[:200]
                                    )
                                    if archived and archived_msg_id:
                                        await asyncio.to_thread(self.db.update_superseded_channel_message_id, match_entry_id, archived_msg_id)
                                    await asyncio.to_thread(self.db.delete_embedding_by_entry_id, match_entry_id)
                                    await asyncio.to_thread(self.db.mark_as_superseded, match_entry_id, entry_id)
                                    superseded = True
                                    placement_reason = (
                                        f"AI categorized as '{category}'. "
                                        f"Reasoning: {reasoning or 'no reasoning provided'} "
                                        f"| Superseded entry {match_entry_id} "
                                        f"(similarity: {similarity_info['similarity']:.3f})"
                                    )
                                    logger.info(
                                        f"Superseding {match_entry_id} with {entry_id} "
                                        f"(reason: {comparison['reasoning']})"
                                    )
                                else:
                                    logger.warning(
                                        f"Failed to delete Discord message {old_mapping['discord_message_id']} "
                                        f"for supersede — aborting supersede of {match_entry_id}"
                                    )

                    if not superseded and not keep_both:
                        logger.info(
                            f"Similar story suppressed (not posted): {entry_id} "
                            f"(score: {similarity_info['similarity']:.3f}, "
                            f"matches: {similarity_info['match_preview'][:100]})"
                        )
                        self.stats['duplicates'] += 1
                        await asyncio.to_thread(self.db.mark_processed, entry_id)
                        await asyncio.to_thread(self.db.add_embedding, content, embedding, entry_id=entry_id)
                        self.media_handler.cleanup_entry_media(entry)
                        return True
                
                # Reaction-worthiness gate (skip for similar/superseded entries).
                # Two directions:
                #   demote — AI assigned a real category but the entry is boring
                #   rescue — AI said ignore but the entry is too interesting to bury
                newsworthiness_score = None
                if not similarity_info and not superseded and not duplicate_info \
                        and getattr(config, 'NEWSWORTHINESS_FILTER_ENABLED', False):
                    if category != 'ignore':
                        logger.debug("Rating reaction-worthiness...")
                        newsworthiness = await asyncio.to_thread(self.ollama.rate_newsworthiness, combined_content, category)
                        newsworthiness_score = newsworthiness['score']

                        if not newsworthiness['passed']:
                            original_category = category
                            category = 'ignore'
                            secondary_category = None
                            reasoning = (
                                f"AI suggested '{original_category}': {reasoning or 'no reasoning provided'} "
                                f"| OVERRIDDEN: newsworthiness filter failed "
                                f"(score: {newsworthiness['score']:.1f}/10, "
                                f"reason: {newsworthiness['reasoning']})"
                            )
                            placement_reason = (
                                f"Content filter: newsworthiness filter failed "
                                f"(score: {newsworthiness['score']:.1f}/10, "
                                f"reason: {newsworthiness['reasoning']}), "
                                f"AI had suggested '{original_category}', routed to ignore"
                            )
                            logger.info(
                                f"Newsworthiness filter: {newsworthiness['score']:.1f}/10 below threshold "
                                f"- routing from '{original_category}' to 'ignore' "
                                f"(reason: {newsworthiness['reasoning']})"
                            )
                    elif _ai_category == 'ignore' and getattr(config, 'HIGH_SCORE_RESCUE_ENABLED', False):
                        # AI said ignore — 28% of those were user-rescued historically.
                        # Rate anyway; a very high reaction score earns a second look.
                        rescue_threshold = getattr(config, 'RESCUE_NEWSWORTHINESS_THRESHOLD', 7.5)
                        logger.debug("Rating reaction-worthiness of AI-ignored entry (rescue check)...")
                        newsworthiness = await asyncio.to_thread(
                            self.ollama.rate_newsworthiness, combined_content, 'general news', rescue_threshold
                        )
                        # A rating error fails OPEN for demotion (safe) but must fail
                        # CLOSED for rescue: otherwise an Ollama hiccup auto-promotes an
                        # AI-ignored entry, and the rescue categorize below can itself fall
                        # back to an arbitrary real category. Don't record a fake score.
                        rescue_rating_ok = not newsworthiness.get('error')
                        newsworthiness_score = newsworthiness['score'] if rescue_rating_ok else None

                        if rescue_rating_ok and newsworthiness['passed']:
                            rescued_cat, rescued_reasoning, rescued_secondary = await asyncio.to_thread(
                                self.ollama.categorize, combined_content, ['ignore']
                            )
                            # Guard the second leg: with 'ignore' excluded, categorize's
                            # error fallback returns an arbitrary real category tagged
                            # 'Error during categorization' — never promote on that.
                            rescued_errored = (rescued_reasoning or '').startswith('Error during categorization')
                            if rescued_cat != 'ignore' and not rescued_errored:
                                category = rescued_cat
                                secondary_category = rescued_secondary
                                reasoning = (
                                    f"AI suggested 'ignore' but reaction gate scored "
                                    f"{newsworthiness['score']:.1f}/10 — rescued as '{rescued_cat}': "
                                    f"{rescued_reasoning or 'no reasoning provided'}"
                                )
                                placement_reason = (
                                    f"Gate rescue: AI said 'ignore' but reaction score "
                                    f"{newsworthiness['score']:.1f}/10 >= {rescue_threshold}, "
                                    f"re-categorized as '{rescued_cat}'"
                                )
                                logger.info(
                                    f"Gate rescue: score {newsworthiness['score']:.1f}/10 "
                                    f"— promoting AI-ignored entry to '{rescued_cat}'"
                                )
                
                # Filter short videos (under threshold) to ignore channel
                if not similarity_info and not superseded and category != 'ignore':
                    if getattr(config, 'SHORT_VIDEO_FILTER_ENABLED', False):
                        video_duration = entry.get('video_duration')
                        if video_duration is not None and video_duration < config.SHORT_VIDEO_THRESHOLD:
                            original_category = category
                            category = 'ignore'
                            secondary_category = None
                            reasoning = (
                                f"AI suggested '{original_category}': {reasoning or 'no reasoning provided'} "
                                f"| OVERRIDDEN: short video ({video_duration:.0f}s < {config.SHORT_VIDEO_THRESHOLD}s threshold)"
                            )
                            placement_reason = (
                                f"Content filter: short video ({video_duration:.0f}s < {config.SHORT_VIDEO_THRESHOLD}s), "
                                f"AI had suggested '{original_category}', routed to ignore"
                            )
                            logger.info(
                                f"Short video filter: {video_duration:.0f}s duration "
                                f"- routing from '{original_category}' to 'ignore'"
                            )
                
                # Filter audience-engagement questions (e.g. "Do you agree?", "What do you think?")
                if not similarity_info and not superseded and category != 'ignore':
                    if getattr(config, 'AUDIENCE_QUESTION_FILTER_ENABLED', False):
                        if is_audience_question(combined_content):
                            original_category = category
                            category = 'ignore'
                            secondary_category = None
                            reasoning = (
                                f"AI suggested '{original_category}': {reasoning or 'no reasoning provided'} "
                                f"| OVERRIDDEN: audience engagement question detected"
                            )
                            placement_reason = (
                                f"Content filter: audience engagement question detected, "
                                f"AI had suggested '{original_category}', routed to ignore"
                            )
                            logger.info(
                                f"Audience question filter: entry ends with reader-engagement question "
                                f"- routing from '{original_category}' to 'ignore'"
                            )

                # Routing decision: pause mode, category graduation, flood guard.
                # Runs AFTER the content filters so gate scores and filter verdicts are
                # recorded honestly even while the bot is paused or a category is
                # still under review.
                if category != 'ignore':
                    auto_post = getattr(config, 'AUTO_POST_CATEGORIES', None)
                    if getattr(config, 'PAUSE_MODE', False):
                        original_category = category
                        category = 'ignore'
                        secondary_category = None
                        reasoning = (
                            f"AI suggested '{original_category}': {reasoning or 'no reasoning provided'} "
                            f"| OVERRIDDEN: Pause mode enabled - all entries routed to ignore"
                        )
                        placement_reason = f"Pause mode override: bot was paused, routed to ignore instead of '{original_category}'"
                        logger.info(f"Pause mode: routing '{original_category}' to 'ignore'")
                    elif auto_post is not None and category not in auto_post:
                        original_category = category
                        category = 'ignore'
                        secondary_category = None
                        reasoning = (
                            f"AI suggested '{original_category}': {reasoning or 'no reasoning provided'} "
                            f"| OVERRIDDEN: category not graduated to auto-post, routed to ignore for review"
                        )
                        placement_reason = (
                            f"Graduation override: '{original_category}' not in AUTO_POST_CATEGORIES, "
                            f"routed to ignore for review"
                        )
                        logger.info(f"Category '{original_category}' not graduated - routing to 'ignore' for review")
                    else:
                        # Flood guard: cap auto-posts per rolling hour
                        max_per_hour = getattr(config, 'MAX_AUTO_POSTS_PER_HOUR', 0)
                        now = time.time()
                        while self._recent_post_times and now - self._recent_post_times[0] > 3600:
                            self._recent_post_times.popleft()
                        if max_per_hour and len(self._recent_post_times) >= max_per_hour:
                            original_category = category
                            category = 'ignore'
                            secondary_category = None
                            reasoning = (
                                f"AI suggested '{original_category}': {reasoning or 'no reasoning provided'} "
                                f"| OVERRIDDEN: rate limit ({max_per_hour} posts/hour) exceeded"
                            )
                            placement_reason = (
                                f"Rate limit override: {max_per_hour} posts in the last hour, "
                                f"routed '{original_category}' to ignore"
                            )
                            logger.warning(
                                f"Flood guard: {max_per_hour} posts in the last hour - "
                                f"routing '{original_category}' entry to 'ignore'"
                            )

                # Strip wire-service prefixes (e.g. "JUST IN:", "BREAKING:", "🚨NEW:")
                # Done here as a final pass to catch ALL sources (RSS, Twitter, Telegram)
                content = strip_wire_prefixes(content)
                entry['content'] = content

                # OCR / placeholder text is for categorization & dedup only — never shown to users.
                # Image-only entries (e.g. forwarded screenshots) post the media with no body text.
                display_content = '' if content_from_ocr else content

                # Post to Discord
                media_files = entry.get('media_files', [])
                video_urls = entry.get('video_urls', [])
                
                logger.debug(f"Posting to Discord: {len(media_files)} files, {len(video_urls)} videos")
                
                success, discord_message_id, discord_channel_id = await self.discord_poster.post_message(
                    category=category,
                    content=display_content,
                    media_files=media_files,
                    video_urls=video_urls,
                    source_type=source_type,
                    entry_id=entry_id,
                    secondary_category=secondary_category
                )
                
                if success:
                    # Mark as processed and store embedding
                    await asyncio.to_thread(self.db.mark_processed, entry_id)
                    # Embedding storage is best-effort: a transient Ollama failure here must NOT
                    # prevent store_message_mapping() below. Otherwise the message we just posted
                    # to Discord becomes permanently orphaned (posted + marked processed, but with
                    # no DB record), so "Entry Info"/re-categorize report "No database record found".
                    try:
                        if (ocr_text or audio_transcript) and combined_content != content:
                            # OCR or transcript added new text — regenerate embedding with full context
                            # for better future duplicate detection.
                            # (Skips the extra Ollama call when content IS the OCR text, e.g. image-only entries)
                            combined_embedding = await asyncio.to_thread(self.ollama.generate_embedding, combined_content)
                            await asyncio.to_thread(self.db.add_embedding, combined_content, combined_embedding, entry_id=entry_id)
                        else:
                            await asyncio.to_thread(self.db.add_embedding, content, embedding, entry_id=entry_id)
                    except Exception as embed_error:
                        # Fall back to the dedup-check embedding already computed earlier this cycle.
                        logger.error(
                            f"Embedding store failed for {entry_id}; falling back to dedup embedding: {embed_error}"
                        )
                        try:
                            await asyncio.to_thread(self.db.add_embedding, content, embedding, entry_id=entry_id)
                        except Exception as fallback_error:
                            logger.error(
                                f"Fallback embedding also failed for {entry_id}; "
                                f"storing mapping without embedding: {fallback_error}"
                            )
                    
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
                        
                        if placement_reason is None:
                            placement_reason = f"AI categorized as '{category}'. Reasoning: {reasoning or 'no reasoning provided'}"

                        await asyncio.to_thread(
                            self.db.store_message_mapping,
                            telegram_entry_id=entry_id,
                            telegram_message_id=entry.get('message_id', 0),
                            discord_channel_id=discord_channel_id,
                            discord_message_id=discord_message_id,
                            content=display_content,
                            source_url=source_url,
                            video_urls=entry.get('video_urls', []),
                            category=category,
                            source_type=source_type,
                            reasoning=reasoning,
                            original_category=_ai_category,
                            placement_reason=placement_reason,
                            secondary_category=secondary_category,
                            original_secondary_category=_ai_secondary_category,
                            newsworthiness_score=newsworthiness_score
                        )

                    # Track post time for the flood guard
                    if category != 'ignore':
                        self._recent_post_times.append(time.time())

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
            
            finally:
                # Always release the processing lock when done
                self._processing_lock.discard(entry_id)
            
        except GalleryDlFailure as e:
            # gallery-dl failed to extract content - add to retry queue
            logger.warning(f"gallery-dl failure for {entry.get('id', 'unknown')}: {e}")
            self.retry_queue.add_entry(entry)
            self.stats['errors'] += 1
            
            # Clean up on error
            try:
                self.media_handler.cleanup_entry_media(entry)
            except Exception:
                pass
            
            return False
            
        except Exception as e:
            logger.error(f"Error processing entry {entry.get('id', 'unknown')}: {e}", exc_info=True)
            self.stats['errors'] += 1
            
            # Clean up on error
            try:
                self.media_handler.cleanup_entry_media(entry)
            except Exception:
                pass
            
            return False
    
    async def poll_cycle(self):
        """Run one polling cycle"""
        logger.info("\n" + "=" * 80)
        logger.info(f"Starting polling cycle at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80)
        
        cycle_start = time.time()

        # Reset per-cycle statistics
        self.stats['duplicates'] = 0
        self.stats['errors'] = 0
        self.stats['processed'] = 0
        self.stats['by_category'] = {}
        
        # Clean up old database entries
        logger.info("Cleaning up old database entries...")
        await asyncio.to_thread(self.db.cleanup_old_entries)

        # Flush Dexerto headlines that have been waiting too long with no follow-up tweet.
        # Guarded so a merger failure can never abort the poll cycle before RSS/Telegram run.
        try:
            await self.dexerto_merger.flush_stale()
        except Exception as e:
            logger.error(f"Error flushing stale Dexerto entries: {e}", exc_info=True)
        
        # Clean up old retry queue entries (older than 24 hours)
        self.retry_queue.cleanup_old_entries(max_age_hours=24)
        
        # Clean up old media files (older than 2 days)
        from utils import cleanup_old_media_files
        cleanup_old_media_files(retention_days=2)

        # Prune excess rotated logs / oversized service logs once every 7 days
        _LOG_CLEANUP_INTERVAL = 7 * 24 * 3600
        _now = time.time()
        if _now - self._last_log_cleanup >= _LOG_CLEANUP_INTERVAL:
            await asyncio.to_thread(cleanup_bot_log)
            self._last_log_cleanup = _now

        # Nightly rebuild: at 3 AM, force-refresh the Ollama prompt cache so it picks
        # up the full day's re-categorization corrections from the database.
        import datetime
        _now_local = datetime.datetime.now()
        if _now_local.hour == 3 and (_now - self._last_nightly_rebuild) > 20 * 3600:
            self.ollama._enhanced_prompt_cache = None
            self._last_nightly_rebuild = _now
            corrections = await asyncio.to_thread(self.db.get_recategorization_examples, limit=100)
            promotions = await asyncio.to_thread(self.db.get_ignore_promotion_examples, limit=100)
            logger.info(
                f"Nightly prompt rebuild: {len(corrections)} category-correction pairs, "
                f"{len(promotions)} ignore-promotion pairs available"
            )

        # Periodic accuracy report: post categorization accuracy to Discord so
        # graduation decisions (PLAN.md) don't depend on running the script manually.
        if getattr(config, 'ACCURACY_REPORT_ENABLED', False):
            report_hour = getattr(config, 'ACCURACY_REPORT_HOUR', 9)
            interval_days = getattr(config, 'ACCURACY_REPORT_INTERVAL_DAYS', 7)
            # 4h slack so the posting hour doesn't drift later each interval
            min_gap = interval_days * 86400 - 4 * 3600
            if _now_local.hour == report_hour and (_now - self._last_accuracy_report) > min_gap:
                # Mark before attempting so a persistent failure can't spam every cycle
                self._last_accuracy_report = _now
                self._write_accuracy_report_state(_now)
                try:
                    from accuracy_report import build_report
                    report = await asyncio.to_thread(
                        build_report, days=interval_days, db_path=config.DB_PATH
                    )
                    await self.discord_poster.post_accuracy_report(report)
                except Exception as e:
                    logger.error(f"Accuracy report failed: {e}")

        # Get database stats
        db_stats = await asyncio.to_thread(self.db.get_stats)
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
            rss_entries = await asyncio.to_thread(self.rss_poller.poll_all_feeds)
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
        
        # Sort entries chronologically (oldest first) so within-cycle dedup/supersede
        # ordering is consistent across sources.
        _TWITTER_EPOCH_MS = 1288834974657  # Twitter/X snowflake epoch (2010-11-04 01:42:54 UTC)

        def get_entry_timestamp(entry):
            """Return a Unix timestamp (seconds) for cross-source sorting.

            Twitter status_ids are snowflakes (~1e18), not epoch seconds. Comparing
            them directly against the Unix timestamps (~1e9) used by Telegram/RSS
            pushed every tweet to one end of the sort regardless of real time.
            Convert the snowflake's embedded millisecond timestamp to seconds so all
            sources share one scale; its sub-second precision still gives thread
            replies a stable order.
            """
            if entry.get('source_type') == 'twitter' and entry.get('status_id'):
                try:
                    return ((int(entry['status_id']) >> 22) + _TWITTER_EPOCH_MS) / 1000.0
                except (ValueError, TypeError):
                    pass
            if 'timestamp' in entry and entry['timestamp']:
                return entry['timestamp']
            elif 'pub_date' in entry and entry['pub_date']:
                # Parse RSS pub_date to timestamp
                from email.utils import parsedate_to_datetime
                try:
                    dt = parsedate_to_datetime(entry['pub_date'])
                    return dt.timestamp()
                except Exception as e:
                    logger.warning(f"Failed to parse pub_date '{entry.get('pub_date')}': {e}")
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

                # Dexerto tweet pair merger: buffer headline tweets and wait for the
                # matching follow-up tweet (blurb + article URL) before posting.
                consumed = await self.dexerto_merger.handle(entry)
                if consumed:
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
                logger.info(f"Processing real-time Telegram message: {entry['id']}")
                await self.process_entry(entry)
            except Exception as e:
                logger.error(f"Error processing Telegram queue: {e}", exc_info=True)
                await asyncio.sleep(1)
    
    async def process_telegram_edits(self):
        """Process edited messages from the Telegram edit queue"""
        while True:
            try:
                edited_entry = await self.telegram_poller.get_queued_edit()
                logger.info(f"Processing edited Telegram message: {edited_entry['id']}")
                await self.process_telegram_edit(edited_entry)
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
                # No mapping means the NewMessage event was never received (Telethon can
                # swallow it during GetDifference catch-up). Treat this edit as a brand
                # new entry so the message still gets processed and posted to Discord.
                logger.info(
                    f"No Discord mapping found for edited Telegram message: {entry_id} "
                    f"- processing as new entry (NewMessage event was likely missed)"
                )
                # If this is part of a multi-image album, route it through the same
                # buffer/flush logic as real-time messages instead of posting it
                # immediately - otherwise each image in the album ends up as its
                # own separate Discord post.
                buffered = await self.telegram_poller.handle_missed_new_message(edited_entry)
                if buffered:
                    return True
                return await self.process_entry(edited_entry)
            
            discord_channel_id = mapping_info['discord_channel_id']
            discord_message_id = mapping_info['discord_message_id']
            old_content = mapping_info.get('content', '')

            # Skip overwriting if the user manually edited this entry via Edit Text
            if mapping_info.get('user_edited'):
                logger.info(f"Skipping Telegram edit for {entry_id} - entry was manually edited by user")
                return True
            
            logger.info(f"Found Discord message mapping: Channel {discord_channel_id}, Message {discord_message_id}")
            
            # Get the edited content
            new_content = edited_entry.get('content', '')

            # Fix ALL CAPS in edited Telegram messages too
            if new_content and getattr(config, 'CAPS_FIX_ENABLED', False):
                if is_all_caps(new_content, threshold=getattr(config, 'CAPS_FIX_THRESHOLD', 0.65)):
                    logger.info(f"ALL CAPS detected in edited Telegram message, running text cleanup...")
                    new_content = await asyncio.to_thread(self.ollama.format_text, new_content)

            # Apply the same final wire-prefix cleanup used for initial posts.
            # This prevents Telegram edit events from re-adding prefixes like
            # "JUST IN:" or "BREAKING:" after the first Discord post was cleaned.
            new_content = strip_wire_prefixes(new_content)

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
                source_type='telegram',
                category=mapping_info.get('category'),
                secondary_category=mapping_info.get('secondary_category')
            )
            
            if success:
                # Content-only update. store_message_mapping() is INSERT OR REPLACE, which
                # rewrites the whole row and nulls any column not re-supplied — that quietly
                # wiped user_reason (and superseded_by / superseded_channel_discord_message_id)
                # on every edit. A targeted field update touches only content and preserves
                # all other metadata plus the original timestamp.
                await asyncio.to_thread(
                    self.db.update_message_mapping_fields,
                    entry_id,
                    content=new_content,
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

        # Prune excess rotated / oversized service logs on startup, then weekly
        await asyncio.to_thread(cleanup_bot_log)
        self._last_log_cleanup = time.time()

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

def kill_existing_instance():
    """Kill any existing bot instance recorded in the PID file."""
    import psutil

    pid_file = os.path.join("data", "bot.pid")
    if not os.path.exists(pid_file):
        return

    try:
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
    except (ValueError, OSError):
        return

    if old_pid == os.getpid():
        return

    try:
        proc = psutil.Process(old_pid)
        # Confirm it's actually a Python process (not a recycled PID)
        if "python" in proc.name().lower():
            logger.info(f"Found existing bot instance (PID {old_pid}), terminating it...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
                logger.info(f"PID {old_pid} terminated cleanly")
            except psutil.TimeoutExpired:
                proc.kill()
                logger.warning(f"PID {old_pid} force-killed after timeout")
        else:
            logger.debug(f"PID {old_pid} from PID file is not a Python process, ignoring")
    except psutil.NoSuchProcess:
        logger.debug(f"PID {old_pid} from PID file is no longer running")
    except Exception as e:
        logger.warning(f"Could not terminate old instance (PID {old_pid}): {e}")


async def main():
    """Main entry point"""
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Kill any leftover instance before starting
    kill_existing_instance()

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
    finally:
        close_db_connection()

