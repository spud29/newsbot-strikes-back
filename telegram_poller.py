"""
Telegram channel poller using Telethon
"""
from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
import asyncio
import json
import os
from utils import logger, retry_with_backoff, clean_text_content, resolve_shortened_urls, remove_emojis, remove_telegram_formatting
import config

class TelegramPoller:
    """Polls Telegram channels for new messages"""
    
    def __init__(self):
        """Initialize Telegram client"""
        self.api_id = config.TELEGRAM_API_ID
        self.api_hash = config.TELEGRAM_API_HASH
        self.channels = config.TELEGRAM_CHANNELS
        self.client = None
        self.last_message_ids_file = config.DB_LAST_MESSAGE_IDS
        self.last_message_ids = self._load_last_message_ids()  # Track last seen message per channel
        self.message_queue = asyncio.Queue()  # Queue for real-time messages
        self.edit_queue = asyncio.Queue()  # Queue for edited messages
        self.event_handlers_setup = False
        
        # Buffer for grouping album messages in real-time
        self.album_buffer = {}  # grouped_id -> list of parsed entries
        self.album_timers = {}  # grouped_id -> asyncio.Task for timeout
        
        logger.info(f"Telegram Poller initialized for {len(self.channels)} channels")
        logger.info(f"Loaded last message IDs for {len(self.last_message_ids)} channels")
    
    async def start(self):
        """Start the Telegram client"""
        if not self.client:
            # Store session in temp directory to avoid OneDrive sync issues
            import tempfile
            session_dir = os.path.join(tempfile.gettempdir(), 'newsbot')
            os.makedirs(session_dir, exist_ok=True)
            session_path = os.path.join(session_dir, 'newsbot_session')
            
            self.client = TelegramClient(session_path, self.api_id, self.api_hash)
            await self.client.start()
            logger.info(f"Telegram client started (session: {session_path})")
            
            # Set up real-time event handlers
            await self.setup_event_handlers()
    
    async def stop(self):
        """Stop the Telegram client"""
        if self.client:
            await self.client.disconnect()
            logger.info("Telegram client stopped")
    
    async def setup_event_handlers(self):
        """Set up real-time event handlers for new messages"""
        if self.event_handlers_setup:
            return
        
        try:
            # Get channel entities
            channel_entities = []
            for channel_name in self.channels:
                try:
                    entity = await self.client.get_entity(channel_name)
                    channel_entities.append(entity)
                    logger.debug(f"Registered event handler for channel: {channel_name}")
                except Exception as e:
                    logger.error(f"Failed to get entity for {channel_name}: {e}")
            
            # Register event handler for new messages from these channels
            @self.client.on(events.NewMessage(chats=channel_entities))
            async def handler(event):
                await self.on_new_message(event)
            
            # Register event handler for edited messages from these channels
            @self.client.on(events.MessageEdited(chats=channel_entities))
            async def edit_handler(event):
                await self.on_message_edited(event)
            
            self.event_handlers_setup = True
            logger.info(f"Real-time event handlers set up for {len(channel_entities)} channels (new & edited messages)")
            
        except Exception as e:
            logger.error(f"Error setting up event handlers: {e}")
    
    async def on_new_message(self, event):
        """
        Handle incoming real-time message from Telegram
        
        Args:
            event: Telethon NewMessage event
        """
        try:
            message = event.message
            
            # Get channel name from the chat
            channel_name = None
            for ch_name in self.channels:
                try:
                    entity = await self.client.get_entity(ch_name)
                    if entity.id == message.peer_id.channel_id:
                        channel_name = ch_name
                        break
                except:
                    continue
            
            if not channel_name:
                logger.warning(f"Received message from unknown channel: {message.peer_id}")
                return
            
            logger.info(f"Real-time message received from {channel_name}: ID {message.id}")
            
            # Parse the message
            parsed_entry = await self._parse_message(message, channel_name)
            
            if parsed_entry:
                # Check if this is part of an album
                grouped_id = parsed_entry.get('grouped_id')
                
                if grouped_id:
                    # This is part of an album - add to buffer
                    await self._buffer_album_message(grouped_id, parsed_entry)
                else:
                    # Single message - add to queue immediately
                    await self.message_queue.put(parsed_entry)
                    logger.debug(f"Queued real-time message: {parsed_entry['id']}")
            
        except Exception as e:
            logger.error(f"Error handling new message: {e}", exc_info=True)
    
    async def on_message_edited(self, event):
        """
        Handle incoming edited message from Telegram
        
        Args:
            event: Telethon MessageEdited event
        """
        try:
            message = event.message
            
            # Get channel name from the chat
            channel_name = None
            for ch_name in self.channels:
                try:
                    entity = await self.client.get_entity(ch_name)
                    if entity.id == message.peer_id.channel_id:
                        channel_name = ch_name
                        break
                except:
                    continue
            
            if not channel_name:
                logger.warning(f"Received edited message from unknown channel: {message.peer_id}")
                return
            
            logger.info(f"Message edited in {channel_name}: ID {message.id}")
            
            # Parse the edited message
            parsed_entry = await self._parse_message(message, channel_name)
            
            if parsed_entry:
                # Add to edit queue (not the regular message queue)
                await self.edit_queue.put(parsed_entry)
                logger.debug(f"Queued edited message: {parsed_entry['id']}")
            
        except Exception as e:
            logger.error(f"Error handling edited message: {e}", exc_info=True)
    
    async def get_queued_message(self):
        """
        Get next message from the queue (non-blocking)
        
        Returns:
            dict: Message entry or None if queue is empty
        """
        try:
            return await asyncio.wait_for(self.message_queue.get(), timeout=0.1)
        except asyncio.TimeoutError:
            return None
    
    async def get_queued_edit(self):
        """
        Get next edited message from the edit queue (non-blocking)
        
        Returns:
            dict: Edited message entry or None if queue is empty
        """
        try:
            return await asyncio.wait_for(self.edit_queue.get(), timeout=0.1)
        except asyncio.TimeoutError:
            return None
    
    async def _buffer_album_message(self, grouped_id, parsed_entry):
        """
        Buffer an album message and set a timer to flush the album
        
        Args:
            grouped_id: The grouped_id of the album
            parsed_entry: The parsed message entry
        """
        logger.debug(f"Buffering album message with grouped_id {grouped_id}")
        
        # Add to buffer
        if grouped_id not in self.album_buffer:
            self.album_buffer[grouped_id] = []
        
        self.album_buffer[grouped_id].append(parsed_entry)
        
        # Cancel existing timer if any
        if grouped_id in self.album_timers:
            self.album_timers[grouped_id].cancel()
        
        # Set a new timer to flush this album after 2 seconds
        # This gives time for all messages in the album to arrive
        self.album_timers[grouped_id] = asyncio.create_task(
            self._flush_album_after_delay(grouped_id, delay=2.0)
        )
    
    async def _flush_album_after_delay(self, grouped_id, delay=2.0):
        """
        Wait for a delay, then flush the album buffer to the queue
        
        Args:
            grouped_id: The grouped_id of the album to flush
            delay: Delay in seconds before flushing
        """
        try:
            await asyncio.sleep(delay)
            await self._flush_album(grouped_id)
        except asyncio.CancelledError:
            # Timer was cancelled because another message arrived
            pass
        except Exception as e:
            logger.error(f"Error flushing album {grouped_id}: {e}", exc_info=True)
    
    async def _flush_album(self, grouped_id):
        """
        Group and flush buffered album messages to the queue
        
        Args:
            grouped_id: The grouped_id of the album to flush
        """
        if grouped_id not in self.album_buffer:
            return
        
        entries = self.album_buffer.pop(grouped_id)
        
        # Clean up timer reference
        if grouped_id in self.album_timers:
            del self.album_timers[grouped_id]
        
        if not entries:
            return
        
        logger.info(f"Flushing album with {len(entries)} messages (grouped_id: {grouped_id})")
        
        # Group the album messages using the same logic as batch polling
        grouped_entries = self._group_albums(entries)
        
        # Add grouped entry to queue
        for entry in grouped_entries:
            await self.message_queue.put(entry)
            logger.debug(f"Queued album entry: {entry['id']}")
    
    def _load_last_message_ids(self):
        """
        Load last message IDs from file
        
        Returns:
            dict: Channel name to last message ID mapping
        """
        try:
            if os.path.exists(self.last_message_ids_file):
                with open(self.last_message_ids_file, 'r') as f:
                    data = json.load(f)
                    logger.debug(f"Loaded last message IDs: {data}")
                    return data
        except Exception as e:
            logger.error(f"Error loading last message IDs: {e}")
        
        return {}
    
    def _save_last_message_ids(self):
        """Save last message IDs to file"""
        try:
            # Ensure data directory exists
            os.makedirs(os.path.dirname(self.last_message_ids_file), exist_ok=True)
            
            with open(self.last_message_ids_file, 'w') as f:
                json.dump(self.last_message_ids, f, indent=2)
            
            logger.debug(f"Saved last message IDs: {self.last_message_ids}")
        except Exception as e:
            logger.error(f"Error saving last message IDs: {e}")
    
    def update_last_message_id(self, entry_id, message_id):
        """
        Update the last message ID for a channel after successful processing
        
        Args:
            entry_id: The entry ID (format: telegram_channelname_messageid)
            message_id: The message ID to update to
        """
        try:
            # Extract channel name from entry_id (format: telegram_channelname_messageid)
            parts = entry_id.split('_')
            if len(parts) >= 3 and parts[0] == 'telegram':
                # Channel name is everything between 'telegram_' and the last '_messageid'
                channel_name = '_'.join(parts[1:-1])
                
                # Update last message ID if this is newer
                current_last_id = self.last_message_ids.get(channel_name, 0)
                if message_id > current_last_id:
                    self.last_message_ids[channel_name] = message_id
                    self._save_last_message_ids()
                    logger.debug(f"Updated last message ID for {channel_name}: {message_id}")
        except Exception as e:
            logger.error(f"Error updating last message ID for {entry_id}: {e}")
    
    @retry_with_backoff(max_retries=3, initial_delay=2)
    async def poll_channel(self, channel_name):
        """
        Poll a single Telegram channel for new messages
        
        Fetches messages newer than the last known message ID for this channel.
        This prevents re-processing old messages after database cleanup removes
        them from processed_ids.json.
        
        Args:
            channel_name: Username or name of the channel
        
        Returns:
            list: List of new message dictionaries
        """
        logger.debug(f"Polling Telegram channel: {channel_name}")
        
        try:
            # Get the channel entity
            channel = await self.client.get_entity(channel_name)
            
            # Get the last known message ID for this channel
            last_message_id = self.last_message_ids.get(channel_name, 0)
            
            # Fetch messages newer than the last known ID
            # Use min_id to only get messages with ID > last_message_id
            if last_message_id > 0:
                logger.debug(f"Fetching messages from {channel_name} after ID {last_message_id}")
                messages = await self.client.get_messages(channel, limit=100, min_id=last_message_id)
            else:
                # First time polling this channel, get last 5 messages
                logger.debug(f"First time polling {channel_name}, fetching last 5 messages")
                messages = await self.client.get_messages(channel, limit=5)
            
            entries = []
            
            for message in messages:
                if message.text or message.media:
                    parsed_entry = await self._parse_message(message, channel_name)
                    if parsed_entry:
                        entries.append(parsed_entry)
            
            logger.info(f"Found {len(entries)} messages in {channel_name}")
            return entries
            
        except Exception as e:
            logger.error(f"Error polling Telegram channel {channel_name}: {e}")
            raise
    
    async def _parse_message(self, message, channel_name):
        """
        Parse a Telegram message
        
        Args:
            message: Telegram message object
            channel_name: Name of the source channel
        
        Returns:
            dict: Parsed message data
        """
        try:
            # Create unique ID
            entry_id = f"telegram_{channel_name}_{message.id}"
            
            # Get message text and clean it
            content = message.text or message.message or ''
            content = clean_text_content(content)
            content = resolve_shortened_urls(content)
            content = remove_emojis(content)
            content = remove_telegram_formatting(content, channel_name)
            
            # Get timestamp
            timestamp = message.date.timestamp() if message.date else None
            
            # Check for media
            has_media = False
            media_type = None
            
            if message.media:
                has_media = True
                if isinstance(message.media, MessageMediaPhoto):
                    media_type = 'photo'
                elif isinstance(message.media, MessageMediaDocument):
                    media_type = 'document'
                else:
                    media_type = 'other'
            
            # Check if part of a grouped media album
            grouped_id = message.grouped_id
            
            parsed = {
                'id': entry_id,
                'message_id': message.id,
                'source': channel_name,
                'source_type': 'telegram',
                'content': content,
                'timestamp': timestamp,
                'has_media': has_media,
                'media_type': media_type,
                'grouped_id': grouped_id,
                'message_obj': message  # Keep reference for media download
            }
            
            logger.debug(f"Parsed Telegram message: {entry_id} - {content[:50]}...")
            return parsed
            
        except Exception as e:
            logger.error(f"Error parsing Telegram message from {channel_name}: {e}")
            return None
    
    async def poll_all_channels(self):
        """
        Poll all configured Telegram channels
        
        Returns:
            list: Combined list of all messages from all channels
        """
        logger.info(f"Polling {len(self.channels)} Telegram channels...")
        
        # Ensure client is started
        await self.start()
        
        all_entries = []
        
        for channel_name in self.channels:
            try:
                entries = await self.poll_channel(channel_name)
                all_entries.extend(entries)
            except Exception as e:
                logger.error(f"Failed to poll Telegram channel {channel_name}: {e}")
                # Continue with other channels
                continue
        
        # Group messages by grouped_id to handle albums
        all_entries = self._group_albums(all_entries)
        
        logger.info(f"Total Telegram messages collected: {len(all_entries)}")
        return all_entries
    
    def _group_albums(self, entries):
        """
        Group messages that are part of media albums
        
        Args:
            entries: List of message entries
        
        Returns:
            list: Entries with albums grouped together
        """
        grouped = {}
        standalone = []
        
        for entry in entries:
            grouped_id = entry.get('grouped_id')
            
            if grouped_id:
                if grouped_id not in grouped:
                    grouped[grouped_id] = []
                grouped[grouped_id].append(entry)
            else:
                standalone.append(entry)
        
        # For grouped albums, combine them into single entries
        result = standalone.copy()
        
        for group_id, group_entries in grouped.items():
            # Use the first message as base, but mark it as album
            base_entry = group_entries[0].copy()
            base_entry['is_album'] = True
            base_entry['album_messages'] = [e['message_obj'] for e in group_entries]
            # Combine content from all messages in album and clean it
            combined_content = ' '.join([e.get('content', '') for e in group_entries if e.get('content')])
            combined_content = clean_text_content(combined_content)
            combined_content = resolve_shortened_urls(combined_content)
            combined_content = remove_emojis(combined_content)
            combined_content = remove_telegram_formatting(combined_content, base_entry.get('source'))
            base_entry['content'] = combined_content
            result.append(base_entry)
        
        return result
    
    def run_poll_all_channels(self):
        """
        Synchronous wrapper for poll_all_channels
        
        Returns:
            list: All collected entries
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(self.poll_all_channels())

