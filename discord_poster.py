"""
Discord poster for sending messages with media attachments
"""
import discord
import os
import asyncio
import re
from utils import logger, retry_with_backoff
import config

class DiscordPoster:
    """Posts messages to Discord channels"""
    
    def __init__(self):
        """Initialize Discord client"""
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True  # Ensure we can see guilds/channels
        
        self.client = discord.Client(intents=intents)
        self.token = config.DISCORD_TOKEN
        self.channels = config.DISCORD_CHANNELS
        self.ready = False
        self._verified_channels = False
        self._client_task = None
        
        @self.client.event
        async def on_ready():
            self.ready = True
            logger.info(f'Discord client logged in as {self.client.user}')
            
            # Verify channel access after connection is established
            if not self._verified_channels:
                await self._verify_channel_access()
                self._verified_channels = True
        
        logger.info("Discord poster initialized")
    
    async def start(self):
        """Start the Discord client"""
        if not self._client_task:
            # Start the Discord client in the background
            self._client_task = asyncio.create_task(self.client.start(self.token))
            logger.info("Discord client starting...")
            
            # Wait for the client to be ready (with timeout)
            for _ in range(50):  # Wait up to 5 seconds
                if self.ready:
                    logger.info("Discord client connected and ready!")
                    break
                await asyncio.sleep(0.1)
            else:
                logger.warning("Discord client hasn't signaled ready yet (this is usually fine)")
    
    async def stop(self):
        """Stop the Discord client"""
        if self.client:
            await self.client.close()
            logger.info("Discord client stopped")
        
        # Cancel the background task
        if self._client_task:
            self._client_task.cancel()
            try:
                await self._client_task
            except asyncio.CancelledError:
                pass
    
    @retry_with_backoff(max_retries=3, initial_delay=2)
    async def post_message(self, category, content, media_files=None, video_urls=None, source_type=None):
        """
        Post a message to Discord channel
        
        Args:
            category: Category name (maps to channel ID)
            content: Text content to post
            media_files: List of file paths to attach
            video_urls: List of video URLs to hide in message
            source_type: Type of source ('twitter' or 'telegram') to determine URL embedding
        
        Returns:
            tuple: (success: bool, discord_message_id: int or None, discord_channel_id: int or None)
        """
        try:
            # Get channel ID from category
            channel_id = self.channels.get(category, self.channels[config.DEFAULT_CATEGORY])
            
            logger.debug(f"Posting to category '{category}' (channel {channel_id})")
            
            # Get the channel
            channel = self.client.get_channel(channel_id)
            
            if not channel:
                logger.error(
                    f"Could not find Discord channel: {channel_id}\n"
                    f"  Category: {category}\n"
                    f"  This usually means:\n"
                    f"    1. The bot is not in the server containing this channel\n"
                    f"    2. The channel ID is incorrect in config.py\n"
                    f"    3. The bot lacks permissions to view the channel"
                )
                return False, None, None
            
            # Prepare the message content
            message_text = content
            
            # Determine whether to suppress embeds using Discord's native API
            suppress_embeds = True  # Default: suppress all embeds
            
            if source_type == 'twitter' and video_urls:
                # Twitter with video: allow embeds so video can play inline
                suppress_embeds = False
                # Add hidden video URLs using markdown (these will embed)
                for video_url in video_urls:
                    # Only add Twitter video URLs (skip Telegram placeholder URLs)
                    if video_url.startswith('http'):
                        message_text += f" [.]({video_url})"
            
            # Discord has a 2000 character limit
            if len(message_text) > 2000:
                logger.warning(f"Message too long ({len(message_text)} chars), truncating to 2000")
                message_text = message_text[:1997] + "..."
            
            # Prepare file attachments
            files = []
            if media_files:
                for file_path in media_files:
                    if os.path.exists(file_path):
                        try:
                            file_size = os.path.getsize(file_path)
                            
                            # Discord has 8MB limit for free, 50MB for nitro
                            # We'll use 8MB as safe limit
                            if file_size > 8 * 1024 * 1024:
                                logger.warning(f"File too large ({file_size} bytes), skipping: {file_path}")
                                continue
                            
                            files.append(discord.File(file_path))
                            logger.debug(f"Attached file: {file_path} ({file_size} bytes)")
                        except Exception as e:
                            logger.error(f"Error preparing file {file_path}: {e}")
            
            # Send the message with embed suppression setting
            sent_message = await channel.send(
                content=message_text, 
                files=files, 
                suppress_embeds=suppress_embeds
            )
            
            logger.info(
                f"Successfully posted to {category}: "
                f"{len(content)} chars, {len(files)} files, Discord ID: {sent_message.id}"
            )
            
            return True, sent_message.id, channel_id
            
        except discord.errors.HTTPException as e:
            if e.status == 429:  # Rate limit
                logger.error(f"Discord rate limit hit: {e}")
                raise  # Let retry decorator handle it
            else:
                logger.error(f"Discord HTTP error: {e}")
                return False, None, None
        except Exception as e:
            logger.error(f"Error posting to Discord: {e}", exc_info=True)
            raise
    
    @retry_with_backoff(max_retries=3, initial_delay=2)
    async def edit_message(self, channel_id, message_id, content, source_type=None):
        """
        Edit an existing Discord message
        
        Args:
            channel_id: Discord channel ID where message is located
            message_id: Discord message ID to edit
            content: New text content
            source_type: Type of source ('twitter' or 'telegram') to determine URL embedding
        
        Returns:
            bool: True if successful
        """
        try:
            logger.debug(f"Editing Discord message {message_id} in channel {channel_id}")
            
            # Get the channel
            channel = self.client.get_channel(channel_id)
            
            if not channel:
                logger.error(f"Could not find Discord channel: {channel_id}")
                return False
            
            # Get the message
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                logger.error(f"Discord message not found: {message_id} in channel {channel_id}")
                return False
            except discord.Forbidden:
                logger.error(f"No permission to access message: {message_id}")
                return False
            
            # Prepare the message content (same logic as post_message)
            message_text = content
            
            # For edits, always suppress embeds by default
            # (edit_message doesn't receive video_urls parameter, so we keep it simple)
            suppress_embeds = True
            
            # Discord has a 2000 character limit
            if len(message_text) > 2000:
                logger.warning(f"Message too long ({len(message_text)} chars), truncating to 2000")
                message_text = message_text[:1997] + "..."
            
            # Edit the message with embed suppression (note: cannot edit attachments, only text)
            await message.edit(content=message_text, suppress=suppress_embeds)
            
            logger.info(f"Successfully edited Discord message {message_id}: {len(content)} chars")
            
            return True
            
        except discord.errors.HTTPException as e:
            if e.status == 429:  # Rate limit
                logger.error(f"Discord rate limit hit: {e}")
                raise  # Let retry decorator handle it
            else:
                logger.error(f"Discord HTTP error: {e}")
                return False
        except Exception as e:
            logger.error(f"Error editing Discord message: {e}", exc_info=True)
            return False
    
    async def _verify_channel_access(self):
        """
        Verify that the bot can access all configured channels
        """
        logger.info("Verifying Discord channel access...")
        
        accessible = []
        inaccessible = []
        
        for category, channel_id in self.channels.items():
            channel = self.client.get_channel(channel_id)
            if channel:
                accessible.append(f"  ✓ {category}: #{channel.name} ({channel_id})")
            else:
                inaccessible.append(f"  ✗ {category}: ID {channel_id} (NOT FOUND)")
        
        if accessible:
            logger.info(f"Accessible channels ({len(accessible)}/{len(self.channels)}):")
            for line in accessible:
                logger.info(line)
        
        if inaccessible:
            logger.warning(f"\nInaccessible channels ({len(inaccessible)}/{len(self.channels)}):")
            for line in inaccessible:
                logger.warning(line)
            logger.warning(
                "\nTo fix inaccessible channels:\n"
                "  1. Invite the bot to the server(s) containing these channels\n"
                "  2. Verify the channel IDs in config.py are correct\n"
                "  3. Ensure the bot has 'View Channels' and 'Send Messages' permissions\n"
                "  4. Check that the bot's role has access to the channels"
            )
            
            # List available guilds and their channels for debugging
            guilds = self.client.guilds
            if guilds:
                logger.info(f"\nBot is in {len(guilds)} server(s):")
                for guild in guilds:
                    logger.info(f"  Server: {guild.name} (ID: {guild.id})")
                    text_channels = [ch for ch in guild.channels if isinstance(ch, discord.TextChannel)]
                    if text_channels:
                        logger.info(f"    Text channels ({len(text_channels)}):")
                        for ch in text_channels[:10]:  # Limit to first 10
                            logger.info(f"      - #{ch.name} (ID: {ch.id})")
                        if len(text_channels) > 10:
                            logger.info(f"      ... and {len(text_channels) - 10} more")
        else:
            logger.info("✓ All configured channels are accessible!")
    
    def get_channel_info(self, category):
        """
        Get information about a Discord channel
        
        Args:
            category: Category name
        
        Returns:
            dict: Channel information or None
        """
        try:
            channel_id = self.channels.get(category)
            if not channel_id:
                return None
            
            channel = self.client.get_channel(channel_id)
            if not channel:
                return None
            
            return {
                'id': channel.id,
                'name': channel.name,
                'type': str(channel.type)
            }
        except Exception as e:
            logger.error(f"Error getting channel info: {e}")
            return None

