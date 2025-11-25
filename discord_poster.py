"""
Discord poster for sending messages with media attachments
"""
import discord
from discord.ui import Button, View, Select
import os
import asyncio
import re
import hashlib
from utils import logger, retry_with_backoff
import config
from vote_tracker import VoteTracker
from removed_entries import RemovedEntriesDB


class PerplexitySearchView(View):
    """Discord UI View containing a Perplexity search button"""
    
    def __init__(self, content, perplexity_client, entry_hash):
        """
        Initialize the view with a search button
        
        Args:
            content: The entry content/headline to search for
            perplexity_client: Instance of PerplexityClient
            entry_hash: Unique hash of the entry for tracking
        """
        super().__init__(timeout=None)  # No timeout for persistent views
        self.content = content
        self.perplexity_client = perplexity_client
        self.entry_hash = entry_hash
        self.search_performed = False
        self.parent_view = None  # Reference to parent CombinedButtonView if exists
        
        # Get button configuration from config
        button_label = getattr(config, 'PERPLEXITY_BUTTON_LABEL', 'Get More Info')
        button_emoji = getattr(config, 'PERPLEXITY_BUTTON_EMOJI', '🔍')
        
        # Map string style to discord.ButtonStyle
        style_map = {
            'primary': discord.ButtonStyle.primary,
            'secondary': discord.ButtonStyle.secondary,
            'success': discord.ButtonStyle.success,
            'danger': discord.ButtonStyle.danger,
        }
        button_style_str = getattr(config, 'PERPLEXITY_BUTTON_STYLE', 'primary')
        button_style = style_map.get(button_style_str, discord.ButtonStyle.primary)
        
        # Create the button with custom_id for persistence
        # Discord custom_id has 100 character limit, so we use a hash
        button = Button(
            style=button_style,
            label=button_label,
            emoji=button_emoji,
            custom_id=f"perplexity_search:{entry_hash}"
        )
        button.callback = self.button_callback
        self.add_item(button)
    
    async def button_callback(self, interaction: discord.Interaction):
        """
        Handle button click - perform Perplexity search and respond
        
        Args:
            interaction: Discord interaction from button click
        """
        try:
            # Check if search was already performed
            if self.search_performed:
                await interaction.response.send_message(
                    "This search has already been performed.",
                    ephemeral=True
                )
                return
            
            # Defer the response to show loading state
            await interaction.response.defer(ephemeral=False)
            
            logger.info(f"Perplexity search button clicked for entry hash: {self.entry_hash}")
            
            # Check if Perplexity client is available
            if not self.perplexity_client or not self.perplexity_client.is_available():
                await interaction.followup.send(
                    "❌ Perplexity search is not available. API key may not be configured.",
                    ephemeral=True
                )
                return
            
            # Perform the search
            result = self.perplexity_client.search(self.content)
            
            if result['success']:
                # Disable the button in the original message
                # Use parent view if this is part of a combined view, otherwise use self
                view_to_update = self.parent_view if self.parent_view else self
                
                # Disable the Perplexity button
                for item in view_to_update.children:
                    if isinstance(item, Button) and hasattr(item, 'custom_id') and item.custom_id and item.custom_id.startswith("perplexity_search:"):
                        item.disabled = True
                
                # Update the original message to disable the button
                try:
                    await interaction.message.edit(view=view_to_update)
                except Exception as e:
                    logger.warning(f"Failed to disable button: {e}")
                
                # Mark search as performed
                self.search_performed = True
                
                # Send the response
                if result.get('answer'):
                    # Send the answer text using a Discord embed for better formatting
                    answer = result['answer']
                    
                    # Discord embed description has 4096 char limit, but we'll keep it shorter
                    max_length = 3900
                    truncated = False
                    if len(answer) > max_length:
                        answer = answer[:max_length] + "..."
                        truncated = True
                    
                    # Create an embed for better presentation
                    embed = discord.Embed(
                        title="🔍 Additional Context from Perplexity AI",
                        description=answer,
                        color=discord.Color.blue()
                    )
                    
                    if truncated:
                        embed.set_footer(text="⚠️ Answer truncated due to length")
                    else:
                        embed.set_footer(text="Powered by Perplexity AI")
                    
                    await interaction.followup.send(embed=embed)
                else:
                    # Fallback: create a search URL
                    search_url = self.perplexity_client.format_search_url(self.content[:200])
                    response_text = f"🔍 **Search on Perplexity:**\n{search_url}"
                    await interaction.followup.send(response_text)
                
                logger.info(f"Perplexity search completed for entry hash: {self.entry_hash}")
            else:
                # Search failed
                error_msg = result.get('error', 'Unknown error')
                await interaction.followup.send(
                    f"❌ Search failed: {error_msg}",
                    ephemeral=True
                )
                logger.error(f"Perplexity search failed: {error_msg}")
        
        except Exception as e:
            logger.error(f"Error in Perplexity button callback: {e}", exc_info=True)
            try:
                await interaction.followup.send(
                    f"❌ An error occurred while processing your search: {str(e)}",
                    ephemeral=True
                )
            except:
                pass


class NotValuableView(View):
    """Discord UI View containing a 'Not Valuable' voting button"""
    
    def __init__(self, entry_id, content, category, discord_channel_id, 
                 vote_tracker, removed_entries_db, database, entry_hash):
        """
        Initialize the view with a voting button
        
        Args:
            entry_id: Entry ID for tracking
            content: The entry content
            category: Category it was posted to
            discord_channel_id: Discord channel ID
            vote_tracker: VoteTracker instance
            removed_entries_db: RemovedEntriesDB instance
            database: Database instance (for removal)
            entry_hash: Unique hash for button custom_id
        """
        super().__init__(timeout=None)  # No timeout for persistent views
        self.entry_id = entry_id
        self.content = content
        self.category = category
        self.discord_channel_id = discord_channel_id
        self.vote_tracker = vote_tracker
        self.removed_entries_db = removed_entries_db
        self.database = database
        self.entry_hash = entry_hash
        self.parent_view = None  # Reference to parent CombinedButtonView if exists
        
        # Get button configuration from config
        button_label = getattr(config, 'NOT_VALUABLE_BUTTON_LABEL', 'Not Valuable')
        button_emoji = getattr(config, 'NOT_VALUABLE_BUTTON_EMOJI', '🗑️')
        
        # Map string style to discord.ButtonStyle
        style_map = {
            'primary': discord.ButtonStyle.primary,
            'secondary': discord.ButtonStyle.secondary,
            'success': discord.ButtonStyle.success,
            'danger': discord.ButtonStyle.danger,
        }
        button_style_str = getattr(config, 'NOT_VALUABLE_BUTTON_STYLE', 'danger')
        button_style = style_map.get(button_style_str, discord.ButtonStyle.danger)
        
        # Create the button with custom_id for persistence
        button = Button(
            style=button_style,
            label=button_label,
            emoji=button_emoji,
            custom_id=f"not_valuable:{entry_hash}"
        )
        button.callback = self.button_callback
        self.add_item(button)
    
    async def button_callback(self, interaction: discord.Interaction):
        """
        Handle button click - track vote and delete if threshold reached
        
        Args:
            interaction: Discord interaction from button click
        """
        try:
            # Defer the response to show loading state
            await interaction.response.defer(ephemeral=True)
            
            voter_user_id = str(interaction.user.id)
            discord_message_id = str(interaction.message.id)
            
            logger.info(f"Not Valuable button clicked by user {voter_user_id} on message {discord_message_id}")
            
            # Add vote and get current count
            entry_data = {
                'entry_id': self.entry_id,
                'content': self.content,
                'category': self.category,
                'discord_channel_id': self.discord_channel_id,
                'discord_message_id': int(discord_message_id)
            }
            
            vote_count, is_duplicate = self.vote_tracker.add_vote(
                discord_message_id, 
                voter_user_id, 
                entry_data
            )
            
            # Check if user already voted
            if is_duplicate:
                await interaction.followup.send(
                    "⚠️ You have already voted on this entry.",
                    ephemeral=True
                )
                return
            
            votes_required = getattr(config, 'NOT_VALUABLE_VOTES_REQUIRED', 2)
            
            # Update button label to show vote count
            # Use parent view if this is part of a combined view, otherwise use self
            view_to_update = self.parent_view if self.parent_view else self
            
            # Update the Not Valuable button label
            for item in view_to_update.children:
                if isinstance(item, Button) and item.custom_id.startswith("not_valuable:"):
                    item.label = f"Not Valuable ({vote_count}/{votes_required})"
            
            try:
                await interaction.message.edit(view=view_to_update)
            except Exception as e:
                logger.warning(f"Failed to update button label: {e}")
            
            # Check if threshold reached
            if vote_count >= votes_required:
                logger.info(f"Vote threshold reached ({vote_count}/{votes_required}) for message {discord_message_id}")
                
                # Get voter IDs
                vote_data = self.vote_tracker.get_votes(discord_message_id)
                voter_ids = vote_data.get('voters', []) if vote_data else []
                
                # Delete the Discord message
                try:
                    await interaction.message.delete()
                    logger.info(f"Deleted Discord message {discord_message_id}")
                except Exception as e:
                    logger.error(f"Failed to delete Discord message: {e}")
                    await interaction.followup.send(
                        f"❌ Failed to delete message: {str(e)}",
                        ephemeral=True
                    )
                    return
                
                # Remove from database
                try:
                    if self.entry_id in self.database.processed_ids:
                        del self.database.processed_ids[self.entry_id]
                        self.database._save_json(self.database.processed_ids_path, self.database.processed_ids)
                    
                    if self.entry_id in self.database.message_mapping:
                        del self.database.message_mapping[self.entry_id]
                        self.database._save_json(self.database.message_mapping_path, self.database.message_mapping)
                    
                    # Also remove embedding if it exists
                    content_hash = hashlib.md5(self.content.encode('utf-8')).hexdigest()
                    if content_hash in self.database.embeddings:
                        del self.database.embeddings[content_hash]
                        self.database._save_json(self.database.embeddings_path, self.database.embeddings)
                    
                    logger.info(f"Removed entry {self.entry_id} from database")
                except Exception as e:
                    logger.error(f"Error removing entry from database: {e}", exc_info=True)
                
                # Store in removed entries database
                try:
                    self.removed_entries_db.add_removed_entry(
                        entry_id=self.entry_id,
                        content=self.content,
                        category=self.category,
                        voter_ids=voter_ids,
                        discord_message_id=int(discord_message_id),
                        discord_channel_id=self.discord_channel_id
                    )
                    logger.info(f"Added entry {self.entry_id} to removed entries database")
                except Exception as e:
                    logger.error(f"Error adding to removed entries: {e}", exc_info=True)
                
                # Clean up vote tracking
                self.vote_tracker.remove_tracking(discord_message_id)
                
                # Send confirmation (to the last voter, since message is deleted)
                try:
                    await interaction.followup.send(
                        f"✅ Entry removed successfully after {vote_count} votes. This content will be used to improve future categorization.",
                        ephemeral=True
                    )
                except:
                    pass  # Message might already be deleted
                
                logger.info(f"Successfully processed removal of entry {self.entry_id}")
            else:
                # Not enough votes yet
                await interaction.followup.send(
                    f"✅ Vote recorded ({vote_count}/{votes_required}). Need {votes_required - vote_count} more vote(s) to remove.",
                    ephemeral=True
                )
        
        except Exception as e:
            logger.error(f"Error in Not Valuable button callback: {e}", exc_info=True)
            try:
                await interaction.followup.send(
                    f"❌ An error occurred: {str(e)}",
                    ephemeral=True
                )
            except:
                pass




class CombinedButtonView(View):
    """Combined view with Perplexity search and Not Valuable buttons"""
    
    def __init__(self, perplexity_view=None, not_valuable_view=None):
        """
        Initialize combined view
        
        Args:
            perplexity_view: PerplexitySearchView instance (optional)
            not_valuable_view: NotValuableView instance (optional)
        """
        super().__init__(timeout=None)
        
        # Store references to child views and set their parent reference
        self.perplexity_view = perplexity_view
        self.not_valuable_view = not_valuable_view
        
        # Add Perplexity button if provided
        if perplexity_view:
            # Set parent reference so child can update combined view
            perplexity_view.parent_view = self
            for item in perplexity_view.children:
                self.add_item(item)
            # Copy callback references
            self.perplexity_content = perplexity_view.content
            self.perplexity_client = perplexity_view.perplexity_client
            self.perplexity_entry_hash = perplexity_view.entry_hash
            self.perplexity_search_performed = False
        
        # Add Not Valuable button if provided
        if not_valuable_view:
            # Set parent reference so child can update combined view
            not_valuable_view.parent_view = self
            for item in not_valuable_view.children:
                self.add_item(item)
            # Copy callback references
            self.entry_id = not_valuable_view.entry_id
            self.content = not_valuable_view.content
            self.category = not_valuable_view.category
            self.discord_channel_id = not_valuable_view.discord_channel_id
            self.vote_tracker = not_valuable_view.vote_tracker
            self.removed_entries_db = not_valuable_view.removed_entries_db
            self.database = not_valuable_view.database
            self.entry_hash = not_valuable_view.entry_hash


class DiscordPoster:
    """Posts messages to Discord channels"""
    
    def __init__(self, perplexity_client=None, database=None, vote_tracker=None, removed_entries_db=None):
        """
        Initialize Discord client
        
        Args:
            perplexity_client: Optional PerplexityClient instance for search buttons
            database: Optional Database instance for entry removal
            vote_tracker: Optional VoteTracker instance
            removed_entries_db: Optional RemovedEntriesDB instance
        """
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True  # Ensure we can see guilds/channels
        
        self.client = discord.Client(intents=intents)
        self.token = config.DISCORD_TOKEN
        self.channels = config.DISCORD_CHANNELS
        self.ready = False
        self._verified_channels = False
        self._client_task = None
        self.perplexity_client = perplexity_client
        
        # Initialize vote tracking and removed entries if not provided
        self.database = database
        self.vote_tracker = vote_tracker if vote_tracker else VoteTracker()
        self.removed_entries_db = removed_entries_db if removed_entries_db else RemovedEntriesDB()
        
        @self.client.event
        async def on_ready():
            self.ready = True
            logger.info(f'Discord client logged in as {self.client.user}')
            
            # Verify channel access after connection is established
            if not self._verified_channels:
                await self._verify_channel_access()
                self._verified_channels = True
        
        @self.client.event
        async def on_message(message):
            """Handle incoming messages for recategorize command"""
            # Ignore messages from the bot itself
            if message.author == self.client.user:
                return
            
            # Check if recategorize command is enabled
            if not getattr(config, 'RECATEGORIZE_COMMAND_ENABLED', True):
                return
            
            # Check if message is a reply
            if not message.reference or not message.reference.message_id:
                return
            
            # Check if user is allowed to recategorize
            allowed_user_ids = getattr(config, 'RECATEGORIZE_ALLOWED_USER_IDS', [])
            if message.author.id not in allowed_user_ids:
                return
            
            # Check if message starts with recategorize command
            command_prefix = getattr(config, 'RECATEGORIZE_COMMAND_PREFIX', '!recategorize')
            if not message.content.strip().lower().startswith(command_prefix.lower()):
                return
            
            # Parse the command to get the new category
            parts = message.content.strip().split(maxsplit=1)
            if len(parts) < 2:
                await message.reply(
                    f"❌ Please specify a category. Usage: `{command_prefix} <category>`\n"
                    f"Available categories: {', '.join(sorted(config.DISCORD_CHANNELS.keys()))}"
                )
                return
            
            new_category = parts[1].strip().lower()
            
            # Validate the category
            if new_category not in config.DISCORD_CHANNELS:
                await message.reply(
                    f"❌ Invalid category: `{new_category}`\n"
                    f"Available categories: {', '.join(sorted(config.DISCORD_CHANNELS.keys()))}"
                )
                return
            
            # Get the message being replied to
            try:
                replied_message = await message.channel.fetch_message(message.reference.message_id)
            except discord.NotFound:
                await message.reply("❌ Could not find the message to re-categorize.")
                return
            except Exception as e:
                logger.error(f"Error fetching replied message: {e}")
                await message.reply(f"❌ Error fetching message: {str(e)}")
                return
            
            # Check if the replied message is from the bot
            if replied_message.author != self.client.user:
                await message.reply("❌ You can only re-categorize messages posted by the bot.")
                return
            
            # Find the entry in the database
            entry_id = None
            entry_data = None
            current_category = None
            
            # Search message_mapping for this Discord message ID
            if self.database:
                for eid, mapping in self.database.message_mapping.items():
                    if mapping.get('discord_message_id') == replied_message.id:
                        entry_id = eid
                        entry_data = mapping
                        current_category = mapping.get('category', 'unknown')
                        break
            
            if not entry_id or not entry_data:
                await message.reply("❌ Could not find entry data for this message in the database.")
                return
            
            # Check if it's already in the target category
            if current_category == new_category:
                await message.reply(f"⚠️ This entry is already in the **{new_category}** category.")
                return
            
            logger.info(
                f"Re-categorize command from user {message.author.id}: "
                f"Moving entry {entry_id} from {current_category} to {new_category}"
            )
            
            # Send a "processing" message
            processing_msg = await message.reply(f"⏳ Re-categorizing from **{current_category}** to **{new_category}**...")
            
            # Perform the re-categorization
            try:
                success, new_message_id, new_channel_id, error_msg = await self.recategorize_entry(
                    message_id=replied_message.id,
                    channel_id=replied_message.channel.id,
                    new_category=new_category,
                    entry_id=entry_id,
                    content=entry_data.get('content', replied_message.content),
                    media_files=None,  # Will download from original message
                    video_urls=entry_data.get('video_urls', []),
                    source_type=None  # Not needed for recategorization
                )
                
                if success:
                    await processing_msg.edit(
                        content=f"✅ Successfully re-categorized from **{current_category}** to **{new_category}**!\n"
                        f"New message ID: {new_message_id}"
                    )
                    logger.info(f"Successfully re-categorized entry {entry_id} to {new_category}")
                else:
                    await processing_msg.edit(
                        content=f"❌ Failed to re-categorize: {error_msg}"
                    )
                    logger.error(f"Failed to re-categorize entry {entry_id}: {error_msg}")
            except Exception as e:
                logger.error(f"Error during re-categorization: {e}", exc_info=True)
                await processing_msg.edit(
                    content=f"❌ An error occurred: {str(e)}"
                )
        
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
    async def post_message(self, category, content, media_files=None, video_urls=None, source_type=None, 
                          enable_perplexity_button=None, enable_not_valuable_button=None, entry_id=None):
        """
        Post a message to Discord channel
        
        Args:
            category: Category name (maps to channel ID)
            content: Text content to post
            media_files: List of file paths to attach
            video_urls: List of video URLs to hide in message
            source_type: Type of source ('twitter' or 'telegram') to determine URL embedding
            enable_perplexity_button: Whether to add Perplexity search button (default: from config)
            enable_not_valuable_button: Whether to add Not Valuable button (default: from config)
            entry_id: Entry ID for tracking (required for Not Valuable button)
        
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
                            
                            # Discord has 25MB limit for free, 50MB for level 2 boost, 100MB for level 3 boost
                            # Check against configured limit
                            max_size = config.DISCORD_FILE_SIZE_LIMIT_MB * 1024 * 1024
                            if file_size > max_size:
                                logger.warning(
                                    f"File too large ({file_size} bytes = {file_size / 1024 / 1024:.1f}MB, "
                                    f"limit: {config.DISCORD_FILE_SIZE_LIMIT_MB}MB), skipping: {file_path}"
                                )
                                continue
                            
                            files.append(discord.File(file_path))
                            logger.debug(f"Attached file: {file_path} ({file_size} bytes)")
                        except Exception as e:
                            logger.error(f"Error preparing file {file_path}: {e}")
            
            # Create combined button view with Perplexity and/or Not Valuable buttons
            view = None
            perplexity_view = None
            not_valuable_view = None
            
            # Determine if buttons should be enabled
            if enable_perplexity_button is None:
                enable_perplexity_button = getattr(config, 'PERPLEXITY_BUTTON_ENABLED', True)
            if enable_not_valuable_button is None:
                enable_not_valuable_button = getattr(config, 'NOT_VALUABLE_BUTTON_ENABLED', True)
            
            # Generate a hash for button custom_id
            entry_hash = hashlib.md5(content.encode('utf-8')).hexdigest()[:16]
            
            # Create Perplexity view if enabled
            if enable_perplexity_button and self.perplexity_client and self.perplexity_client.is_available():
                perplexity_view = PerplexitySearchView(content, self.perplexity_client, entry_hash)
                logger.debug(f"Adding Perplexity search button to message")
            
            # Create Not Valuable view if enabled and entry_id provided
            if (enable_not_valuable_button and entry_id and self.database and 
                self.vote_tracker and self.removed_entries_db):
                not_valuable_view = NotValuableView(
                    entry_id=entry_id,
                    content=content,
                    category=category,
                    discord_channel_id=channel_id,
                    vote_tracker=self.vote_tracker,
                    removed_entries_db=self.removed_entries_db,
                    database=self.database,
                    entry_hash=entry_hash
                )
                logger.debug(f"Adding Not Valuable button to message")
            
            # Combine views based on which are enabled
            views_list = [v for v in [perplexity_view, not_valuable_view] if v is not None]
            
            if len(views_list) >= 2:
                # Use CombinedButtonView for multiple buttons
                view = CombinedButtonView(perplexity_view, not_valuable_view)
            elif len(views_list) == 1:
                # Use single view directly
                view = views_list[0]
            
            # Send the message with embed suppression setting and optional button view
            sent_message = await channel.send(
                content=message_text, 
                files=files, 
                suppress_embeds=suppress_embeds,
                view=view
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
            
            # Download attachments if media_files not provided
            downloaded_files = []
            if not media_files and original_message.attachments:
                logger.info(f"Downloading {len(original_message.attachments)} attachments from original message")
                import aiohttp
                import tempfile
                
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
            
            # Update database message mapping
            if self.database and entry_id:
                try:
                    # Update the message mapping with new Discord message ID and channel
                    if entry_id in self.database.message_mapping:
                        self.database.message_mapping[entry_id]['discord_message_id'] = new_message_id
                        self.database.message_mapping[entry_id]['discord_channel_id'] = new_channel_id
                        self.database.message_mapping[entry_id]['category'] = new_category
                        self.database._save_json(self.database.message_mapping_path, self.database.message_mapping)
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

