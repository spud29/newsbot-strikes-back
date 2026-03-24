"""
Discord poster for sending messages with media attachments
Core DiscordPoster class with message posting, editing, and recategorization
"""
import discord
from discord import app_commands
import os
import asyncio
import re
import aiohttp
import tempfile
from utils import logger, retry_with_backoff, ensure_url_on_own_line
import config
from removed_entries import RemovedEntriesDB
from discord_commands import register_commands


class DiscordPoster:
    """Posts messages to Discord channels with context menu command support"""

    def __init__(self, perplexity_client=None, database=None, removed_entries_db=None):
        """
        Initialize Discord client with app commands support

        Args:
            perplexity_client: Optional PerplexityClient instance for search commands
            database: Optional Database instance for entry removal
            removed_entries_db: Optional RemovedEntriesDB instance
        """
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True  # Ensure we can see guilds/channels

        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)

        self.token = config.DISCORD_TOKEN
        self.channels = config.DISCORD_CHANNELS
        self.ready = False
        self._verified_channels = False
        self._client_task = None
        self.perplexity_client = perplexity_client

        # Initialize database and removed entries if not provided
        self.database = database
        self.removed_entries_db = removed_entries_db if removed_entries_db else RemovedEntriesDB()

        self.reprocess_callback = None  # Set by NewsAggregatorBot.start()

        # Register context menu commands
        register_commands(self)

        # Add error handler for app commands
        @self.tree.error
        async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
            logger.error(f"App command error: {error}", exc_info=True)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        f"❌ An error occurred: {str(error)}",
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        f"❌ An error occurred: {str(error)}",
                        ephemeral=True
                    )
            except Exception as e:
                logger.error(f"Failed to send error message: {e}")

        @self.client.event
        async def on_ready():
            self.ready = True
            logger.info(f'Discord client logged in as {self.client.user}')

            # Sync commands with Discord
            try:
                logger.info("Syncing application commands with Discord...")
                # Log registered commands before sync
                logger.debug(f"Registered commands: {[cmd.name for cmd in self.tree.get_commands()]}")
                synced = await self.tree.sync()
                logger.info(f"Successfully synced {len(synced)} application command(s)")
                for cmd in synced:
                    logger.debug(f"  - {cmd.name} (type: {cmd.type})")
            except Exception as e:
                logger.error(f"Failed to sync commands: {e}", exc_info=True)

            # Verify channel access after connection is established
            if not self._verified_channels:
                await self._verify_channel_access()
                self._verified_channels = True

        @self.client.event
        async def on_interaction(interaction: discord.Interaction):
            """Handle all interactions (for logging/debugging)"""
            cmd_name = interaction.data.get('name', 'unknown') if interaction.data else 'unknown'
            logger.debug(f"Interaction received: type={interaction.type}, command={cmd_name}")
            # App commands (context menus) are handled automatically by the command tree
            # This is just for logging

        @self.client.event
        async def on_message(message):
            """Handle incoming messages (currently unused but kept for future extensibility)"""
            # Ignore messages from the bot itself
            if message.author == self.client.user:
                return

        logger.info("Discord poster initialized with app commands support")

    async def _extract_thread_perplexity_content(self, thread):
        """
        Extract Perplexity AI response and citations from a thread

        Args:
            thread: Discord thread to extract from

        Returns:
            dict or None: Thread data including embeds, or None if no Perplexity content found
        """
        try:
            thread_data = {
                'thread_name': thread.name,
                'answer_embed': None,
                'citations_embed': None
            }

            # Fetch messages from the thread
            messages = []
            async for msg in thread.history(limit=10):
                messages.append(msg)

            # Look for Perplexity embeds
            for msg in messages:
                for embed in msg.embeds:
                    if embed.title == "Additional Context from Perplexity AI":
                        thread_data['answer_embed'] = embed
                    elif embed.title == "📚 Sources & Citations":
                        thread_data['citations_embed'] = embed

            # Return None if no Perplexity content found
            if not thread_data['answer_embed']:
                return None

            return thread_data

        except Exception as e:
            logger.error(f"Error extracting thread content: {e}", exc_info=True)
            return None

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
    async def post_message(self, category, content, media_files=None, video_urls=None, source_type=None,
                          enable_perplexity_button=None, enable_not_valuable_button=None, entry_id=None):
        """
        Post a message to Discord channel (now without buttons - using context menu commands)

        Args:
            category: Category name (maps to channel ID)
            content: Text content to post
            media_files: List of file paths to attach
            video_urls: List of video URLs to hide in message
            source_type: Type of source ('twitter' or 'telegram') to determine URL embedding
            enable_perplexity_button: Ignored (kept for backward compatibility)
            enable_not_valuable_button: Ignored (kept for backward compatibility)
            entry_id: Entry ID for tracking

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

            # Ensure URLs are on their own line (fixes formatting when emoji removal
            # or text cleaning causes URLs to be glued to preceding text)
            message_text = ensure_url_on_own_line(message_text)

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

            # Prepare file attachments
            files = []
            if media_files:
                # Use the server's actual file size limit (based on boost level) instead of hardcoded config
                guild = channel.guild
                if guild:
                    max_size = guild.filesize_limit
                    logger.info(
                        f"Server '{guild.name}' boost level: {guild.premium_tier}, "
                        f"actual file size limit: {max_size / 1024 / 1024:.1f}MB"
                    )
                else:
                    max_size = config.DISCORD_FILE_SIZE_LIMIT_MB * 1024 * 1024
                    logger.warning(f"Could not determine guild, using config limit: {config.DISCORD_FILE_SIZE_LIMIT_MB}MB")

                for file_path in media_files:
                    if os.path.exists(file_path):
                        try:
                            file_size = os.path.getsize(file_path)

                            if file_size > max_size:
                                logger.warning(
                                    f"File too large ({file_size} bytes = {file_size / 1024 / 1024:.1f}MB, "
                                    f"server limit: {max_size / 1024 / 1024:.1f}MB), skipping: {file_path}"
                                )
                                continue

                            files.append(discord.File(file_path))
                            logger.debug(f"Attached file: {file_path} ({file_size} bytes)")
                        except Exception as e:
                            logger.error(f"Error preparing file {file_path}: {e}")

            # Send the message (no view/buttons needed - using context menu commands)
            if source_type == 'telegram' and len(message_text) > 2000:
                # Post as embed to avoid Discord's 2000-char plain text limit
                # (Embeds support up to 4096 chars in description)
                if len(message_text) > 4093:
                    message_text = message_text[:4093] + "..."
                embed = discord.Embed(description=message_text, color=discord.Color.dark_grey())
                sent_message = await channel.send(embed=embed, files=files)
            else:
                if len(message_text) > 2000:
                    logger.warning(f"Message too long ({len(message_text)} chars), truncating to 2000")
                    message_text = message_text[:1997] + "..."
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
            elif e.status == 413:  # Payload too large
                logger.error(
                    f"Discord rejected upload as too large (413): {e}\n"
                    f"  Entry: {entry_id}\n"
                    f"  Files attempted: {media_files}\n"
                    f"  This is a permanent error - the file(s) exceed the server's upload limit."
                )
                # Try posting without the files so the text content still gets through
                try:
                    logger.info(f"Retrying post for {entry_id} without file attachments...")
                    sent_message = await channel.send(
                        content=message_text,
                        suppress_embeds=suppress_embeds
                    )
                    logger.info(
                        f"Successfully posted to {category} (without files): "
                        f"{len(content)} chars, Discord ID: {sent_message.id}"
                    )
                    return True, sent_message.id, channel_id
                except Exception as fallback_error:
                    logger.error(f"Fallback post without files also failed: {fallback_error}")
                    return False, None, None
            else:
                logger.error(f"Discord HTTP error: {e}")
                return False, None, None
        except Exception as e:
            logger.error(f"Error posting to Discord: {e}", exc_info=True)
            raise

    async def recategorize_entry(self, message_id, channel_id, new_category, entry_id, content,
                                  media_files=None, video_urls=None, source_type=None):
        """
        Move a Discord message to a different category channel

        Args:
            message_id: Original Discord message ID
            channel_id: Original Discord channel ID
            new_category: New category to move to
            entry_id: Entry ID for tracking
            content: Message content
            media_files: Optional list of media file paths
            video_urls: Optional list of video URLs
            source_type: Type of source ('twitter' or 'telegram')

        Returns:
            tuple: (success, new_message_id, new_channel_id, error_msg)
        """
        try:
            logger.info(f"Re-categorizing message {message_id} from channel {channel_id} to category {new_category}")

            # Get the original channel
            old_channel = self.client.get_channel(channel_id)
            if not old_channel:
                return False, None, None, f"Could not find original channel: {channel_id}"

            # Fetch the original message
            try:
                original_message = await old_channel.fetch_message(message_id)
            except discord.NotFound:
                return False, None, None, f"Original message not found: {message_id}"
            except Exception as e:
                return False, None, None, f"Error fetching original message: {str(e)}"

            # Check if the message has a thread and extract Perplexity content
            thread_data = None
            if original_message.thread:
                logger.info(f"Message {message_id} has a thread: {original_message.thread.id}")
                try:
                    thread_data = await self._extract_thread_perplexity_content(original_message.thread)
                    if thread_data:
                        logger.info(f"Extracted Perplexity content from thread {original_message.thread.id}")
                    else:
                        logger.debug(f"Thread {original_message.thread.id} exists but contains no Perplexity content")
                except Exception as e:
                    logger.error(f"Error extracting thread content: {e}", exc_info=True)
                    # Continue with re-categorization even if thread extraction fails

            # Download attachments if media_files not provided
            downloaded_files = []
            if not media_files and original_message.attachments:
                logger.info(f"Downloading {len(original_message.attachments)} attachments from original message")

                for attachment in original_message.attachments:
                    try:
                        # Create a temporary file
                        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(attachment.filename)[1])

                        # Download the attachment
                        async with aiohttp.ClientSession() as session:
                            async with session.get(attachment.url) as resp:
                                if resp.status == 200:
                                    temp_file.write(await resp.read())
                                    temp_file.close()
                                    downloaded_files.append(temp_file.name)
                                    logger.debug(f"Downloaded attachment: {attachment.filename} to {temp_file.name}")
                    except Exception as e:
                        logger.error(f"Error downloading attachment {attachment.filename}: {e}")

                media_files = downloaded_files if downloaded_files else None

            # Delete the original message
            try:
                await original_message.delete()
                logger.info(f"Deleted original message {message_id} from channel {channel_id}")
            except Exception as e:
                # Clean up downloaded files
                for file_path in downloaded_files:
                    try:
                        os.remove(file_path)
                    except:
                        pass
                return False, None, None, f"Error deleting original message: {str(e)}"

            # Post to new channel
            success, new_message_id, new_channel_id = await self.post_message(
                category=new_category,
                content=content,
                media_files=media_files,
                video_urls=video_urls,
                source_type=source_type,
                entry_id=entry_id
            )

            # Clean up downloaded temporary files
            for file_path in downloaded_files:
                try:
                    os.remove(file_path)
                    logger.debug(f"Cleaned up temporary file: {file_path}")
                except Exception as e:
                    logger.warning(f"Could not delete temporary file {file_path}: {e}")

            if not success:
                return False, None, None, "Error posting to new channel"

            # Recreate thread with Perplexity content if it was extracted
            if thread_data:
                try:
                    logger.info(f"Recreating thread on new message {new_message_id} with Perplexity content")

                    # Get the new channel and message
                    new_channel = self.client.get_channel(new_channel_id)
                    if new_channel:
                        new_message = await new_channel.fetch_message(new_message_id)

                        # Create thread with original name
                        thread = await new_message.create_thread(
                            name=thread_data['thread_name'],
                            auto_archive_duration=1440  # 24 hours
                        )
                        logger.info(f"Created thread {thread.id} on new message")

                        # Post the answer embed
                        await thread.send(embed=thread_data['answer_embed'])
                        logger.debug("Posted answer embed to new thread")

                        # Post citations embed if it exists
                        if thread_data.get('citations_embed'):
                            await thread.send(embed=thread_data['citations_embed'])
                            logger.debug("Posted citations embed to new thread")

                        logger.info(f"Successfully recreated thread with Perplexity content on message {new_message_id}")
                    else:
                        logger.error(f"Could not find new channel {new_channel_id} to recreate thread")

                except discord.Forbidden:
                    logger.error("Bot lacks permission to create threads on new message")
                except Exception as e:
                    logger.error(f"Error recreating thread: {e}", exc_info=True)
                    # Don't fail the whole operation if thread recreation fails

            # Update database message mapping
            if self.database and entry_id:
                try:
                    # Update the message mapping with new Discord message ID and channel
                    self.database.update_message_mapping_fields(
                        entry_id,
                        discord_message_id=new_message_id,
                        discord_channel_id=new_channel_id,
                        category=new_category
                    )
                    logger.info(f"Updated message mapping for entry {entry_id}")
                except Exception as e:
                    logger.error(f"Error updating database: {e}")
                    # Don't fail the whole operation if database update fails

            logger.info(f"Successfully re-categorized entry {entry_id} to {new_category}, new message ID: {new_message_id}")
            return True, new_message_id, new_channel_id, None

        except Exception as e:
            logger.error(f"Error in recategorize_entry: {e}", exc_info=True)
            return False, None, None, str(e)

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

            # Ensure URLs are on their own line (fixes formatting when emoji removal
            # or text cleaning causes URLs to be glued to preceding text)
            message_text = ensure_url_on_own_line(message_text)

            # For edits, always suppress embeds by default
            # (edit_message doesn't receive video_urls parameter, so we keep it simple)
            suppress_embeds = True

            # Edit the message — match the format it was originally posted in
            if message.embeds and not message.content:
                # Originally posted as a Discord embed (long Telegram) — update the embed description
                if len(message_text) > 4093:
                    message_text = message_text[:4093] + "..."
                updated_embed = discord.Embed(description=message_text, color=discord.Color.dark_grey())
                await message.edit(embed=updated_embed)
            else:
                # Plain text — existing path
                if len(message_text) > 2000:
                    logger.warning(f"Message too long ({len(message_text)} chars), truncating to 2000")
                    message_text = message_text[:1997] + "..."
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

    async def delete_message(self, channel_id, message_id):
        """
        Delete a Discord message by channel and message ID.

        Args:
            channel_id: Discord channel ID
            message_id: Discord message ID to delete

        Returns:
            bool: True if deleted (or already gone), False on error
        """
        try:
            channel = self.client.get_channel(channel_id)
            if not channel:
                logger.error(f"delete_message: could not find channel {channel_id}")
                return False
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                logger.warning(f"delete_message: message {message_id} already gone")
                return True
            except discord.Forbidden:
                logger.error(f"delete_message: no permission to fetch message {message_id}")
                return False
            await message.delete()
            logger.info(f"Deleted Discord message {message_id} from channel {channel_id}")
            return True
        except Exception as e:
            logger.error(f"delete_message error: {e}", exc_info=True)
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
