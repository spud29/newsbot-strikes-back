"""
Discord poster for sending messages with media attachments
Now using context menu commands instead of buttons for cleaner UI
"""
import discord
from discord import app_commands
import os
import asyncio
import re
import hashlib
from utils import logger, retry_with_backoff
import config
from vote_tracker import VoteTracker
from removed_entries import RemovedEntriesDB


class DiscordPoster:
    """Posts messages to Discord channels with context menu command support"""
    
    def __init__(self, perplexity_client=None, database=None, vote_tracker=None, removed_entries_db=None):
        """
        Initialize Discord client with app commands support
        
        Args:
            perplexity_client: Optional PerplexityClient instance for search commands
            database: Optional Database instance for entry removal
            vote_tracker: Optional VoteTracker instance
            removed_entries_db: Optional RemovedEntriesDB instance
        """
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True  # Ensure we can see guilds/channels
        
        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)
        
        # Store reference to self for command closures  
        _poster_ref = self
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
        
        # Register context menu commands
        self._register_commands()
        
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
            logger.debug(f"Interaction received: type={interaction.type}, data={interaction.data}")
            # App commands (context menus) are handled automatically by the command tree
            # This is just for logging
        
        @self.client.event
        async def on_message(message):
            """Handle incoming messages (currently unused but kept for future extensibility)"""
            # Ignore messages from the bot itself
            if message.author == self.client.user:
                return
        
        logger.info("Discord poster initialized with app commands support")
    
    def _generate_thread_title(self, content):
        """
        Generate a concise thread title from content using Ollama
        
        Args:
            content: The content to summarize
            
        Returns:
            str: A short thread title (max 100 chars for Discord)
        """
        try:
            # Use Ollama to generate a very short summary for the thread title
            import requests
            
            prompt = f"""Summarize this news headline in 5-8 words for a thread title. Be concise and capture the main topic. 

IMPORTANT RULES:
- Use ONLY plain text words (no emojis, no special characters)
- Do not use quotes or punctuation at the end
- Keep it simple and descriptive
- Example: "Company Layoffs Increase 44 Percent"

News: {content[:500]}

Thread title:"""
            
            payload = {
                "model": config.OLLAMA_CATEGORIZATION_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 30  # Limit tokens for short response
                }
            }
            
            response = requests.post(
                f"{config.OLLAMA_BASE_URL}/api/generate",
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                title = result.get('response', '').strip()
                
                # Clean up the title
                title = title.replace('"', '').replace("'", "").strip()
                
                # Remove any leading/trailing emojis and validate it contains actual text
                # Check if title is empty or contains only emojis/special chars
                import re
                # Remove all emojis and special characters to check if there's actual text
                text_only = re.sub(r'[^\w\s]', '', title)
                
                if not text_only.strip():
                    # Title is empty or only contains emojis/special chars
                    logger.warning(f"Ollama returned invalid title (no text): '{title}', using fallback")
                    # Fallback to simple truncation of content
                    simple_title = content[:70].strip()
                    if len(content) > 70:
                        simple_title += "..."
                    return f"🔍 {simple_title}"
                
                # Ensure it fits Discord's 100 char limit (with emoji prefix)
                max_length = 97  # Leave room for emoji
                if len(title) > max_length:
                    title = title[:max_length-3] + "..."
                
                # Add emoji and return
                return f"🔍 {title}"
            else:
                logger.warning(f"Failed to generate thread title, using default")
                return "🔍 Additional Context"
                
        except Exception as e:
            logger.error(f"Error generating thread title: {e}")
            # Fallback to simple truncation of content
            simple_title = content[:70].strip()
            if len(content) > 70:
                simple_title += "..."
            return f"🔍 {simple_title}"
    
    async def _extract_thread_perplexity_content(self, thread):
        """
        Extract Perplexity AI response from a thread
        
        Args:
            thread: Discord thread object
        
        Returns:
            dict or None: {
                'thread_name': str,
                'answer_embed': discord.Embed,
                'citations_embed': discord.Embed or None
            }
        """
        try:
            logger.debug(f"Extracting Perplexity content from thread {thread.id} (name: {thread.name})")
            
            answer_embed = None
            citations_embed = None
            thread_name = thread.name
            
            # Fetch messages from the thread
            message_count = 0
            async for message in thread.history(limit=50):
                message_count += 1
                
                # Check if message has embeds
                if message.embeds:
                    for embed in message.embeds:
                        # Look for Perplexity answer embed
                        if embed.title == "Additional Context from Perplexity AI":
                            logger.debug(f"Found Perplexity answer embed in thread {thread.id}")
                            answer_embed = embed
                        
                        # Look for citations embed
                        elif embed.title == "📚 Sources & Citations":
                            logger.debug(f"Found citations embed in thread {thread.id}")
                            citations_embed = embed
            
            logger.debug(f"Scanned {message_count} messages in thread {thread.id}")
            
            # Return data if we found Perplexity content
            if answer_embed:
                result = {
                    'thread_name': thread_name,
                    'answer_embed': answer_embed,
                    'citations_embed': citations_embed
                }
                logger.info(f"Successfully extracted Perplexity content from thread {thread.id}")
                return result
            else:
                logger.debug(f"No Perplexity content found in thread {thread.id}")
                return None
                
        except Exception as e:
            logger.error(f"Error extracting thread content: {e}", exc_info=True)
            return None
    
    def _register_commands(self):
        """Register context menu commands"""
        
        # Store reference to self for use in command closures
        poster = self
        
        # Define the command function
        async def get_more_info(interaction: discord.Interaction, message: discord.Message):
            """Context menu command to get additional information via Perplexity AI"""
            logger.debug(f"'Get More Info' command triggered by user {interaction.user.id} on message {message.id}")
            try:
                # Defer response IMMEDIATELY to avoid timeout (Discord gives 3 seconds)
                logger.debug("Deferring interaction response...")
                await interaction.response.defer(ephemeral=True)
                logger.debug("Interaction deferred successfully")
                
                # Check if Perplexity is enabled
                enable_perplexity = getattr(config, 'PERPLEXITY_BUTTON_ENABLED', True)
                if not enable_perplexity or not poster.perplexity_client or not poster.perplexity_client.is_available():
                    await interaction.followup.send(
                        "❌ Perplexity search is not available. API key may not be configured.",
                        ephemeral=True
                    )
                    return
                
                # Check if message is from the bot
                if message.author != poster.client.user:
                    await interaction.followup.send(
                        "❌ This command only works on messages posted by the bot.",
                        ephemeral=True
                    )
                    return
                
                content = message.content
                if not content:
                    await interaction.followup.send(
                        "❌ No content found in message.",
                        ephemeral=True
                    )
                    return
                
                logger.info(f"'Get More Info' command invoked by user {interaction.user.id} on message {message.id}")
                
                # Perform the Perplexity search
                result = poster.perplexity_client.search(content)
                
                if result['success'] and result.get('answer'):
                    answer = result['answer']
                    citations = result.get('citations', [])
                    
                    # Generate a descriptive thread name based on content
                    logger.debug("Generating thread title from content...")
                    thread_name = poster._generate_thread_title(content)
                    logger.debug(f"Generated thread title: {thread_name}")
                    
                    try:
                        thread = await message.create_thread(
                            name=thread_name,
                            auto_archive_duration=1440  # 24 hours
                        )
                        logger.info(f"Created thread {thread.id} for Perplexity response")
                        
                        # Truncate if needed
                        max_length = 3900
                        truncated = False
                        if len(answer) > max_length:
                            answer = answer[:max_length] + "..."
                            truncated = True
                        
                        # Create embed for answer
                        embed = discord.Embed(
                            title="Additional Context from Perplexity AI",
                            description=answer,
                            color=discord.Color.blue()
                        )
                        
                        if truncated:
                            embed.set_footer(text="⚠️ Answer truncated due to length")
                        else:
                            embed.set_footer(text="Powered by Perplexity AI")
                        
                        await thread.send(embed=embed)
                        
                        # Add citations if available
                        if citations:
                            logger.info(f"Adding {len(citations)} citations to thread")
                            citations_text = ""
                            
                            if isinstance(citations, list):
                                for i, citation in enumerate(citations, 1):
                                    if isinstance(citation, dict):
                                        url = citation.get('url', citation.get('link', ''))
                                        title = citation.get('title', citation.get('name', citation.get('domain', 'Source')))
                                        if url:
                                            citations_text += f"{i}. [{title}]({url})\n"
                                        else:
                                            citations_text += f"{i}. {title}\n"
                                    elif isinstance(citation, str):
                                        citations_text += f"{i}. {citation}\n"
                                    else:
                                        citations_text += f"{i}. {str(citation)}\n"
                            
                            if len(citations_text) > 3900:
                                citations_text = citations_text[:3900] + "..."
                            
                            citations_embed = discord.Embed(
                                title="📚 Sources & Citations",
                                description=citations_text if citations_text else "No citations available.",
                                color=discord.Color.green()
                            )
                            citations_embed.set_footer(text=f"{len(citations)} source(s)")
                            await thread.send(embed=citations_embed)
                        
                        await interaction.followup.send(
                            f"✅ Additional context posted in thread: {thread.mention}",
                            ephemeral=True
                        )
                        logger.info(f"Successfully posted Perplexity response in thread {thread.id}")
                        
                    except discord.Forbidden:
                        logger.error("Bot lacks permission to create threads")
                        await interaction.followup.send(
                            "❌ Unable to create thread. Bot may lack thread permissions.",
                            ephemeral=True
                        )
                    except discord.HTTPException as e:
                        logger.error(f"Failed to create thread: {e}")
                        await interaction.followup.send(
                            f"❌ Failed to create thread: {str(e)}",
                            ephemeral=True
                        )
                else:
                    error_msg = result.get('error', 'Unknown error')
                    await interaction.followup.send(
                        f"❌ Search failed: {error_msg}",
                        ephemeral=True
                    )
                    logger.error(f"Perplexity search failed: {error_msg}")
                    
            except Exception as e:
                logger.error(f"Error in 'Get More Info' command: {e}", exc_info=True)
                try:
                    if interaction.response.is_done():
                        await interaction.followup.send(
                            f"❌ An error occurred: {str(e)}",
                            ephemeral=True
                        )
                    else:
                        await interaction.response.send_message(
                            f"❌ An error occurred: {str(e)}",
                            ephemeral=True
                        )
                except:
                    pass
        
        # Manually add the command to the tree
        get_more_info_cmd = app_commands.ContextMenu(
            name="Get More Info",
            callback=get_more_info
        )
        self.tree.add_command(get_more_info_cmd)
        logger.debug("Registered 'Get More Info' context menu command")
        
        # Define the Not Valuable command function
        async def not_valuable(interaction: discord.Interaction, message: discord.Message):
            """Context menu command to vote that a message is not valuable"""
            logger.debug(f"'Not Valuable' command triggered by user {interaction.user.id} on message {message.id}")
            try:
                # Defer response IMMEDIATELY to avoid timeout (Discord gives 3 seconds)
                logger.debug("Deferring interaction response...")
                await interaction.response.defer(ephemeral=True)
                logger.debug("Interaction deferred successfully")
                
                # Check if Not Valuable is enabled
                enable_not_valuable = getattr(config, 'NOT_VALUABLE_BUTTON_ENABLED', True)
                if not enable_not_valuable:
                    await interaction.followup.send(
                        "❌ This feature is not enabled.",
                        ephemeral=True
                    )
                    return
                
                # Check if message is from the bot
                if message.author != poster.client.user:
                    await interaction.followup.send(
                        "❌ This command only works on messages posted by the bot.",
                        ephemeral=True
                    )
                    return
                
                voter_user_id = str(interaction.user.id)
                discord_message_id = str(message.id)
                discord_channel_id = message.channel.id
                content = message.content
                
                logger.info(f"'Not Valuable' command invoked by user {voter_user_id} on message {discord_message_id}")
                
                # Find entry data from database
                entry_id = None
                category = None
                
                if poster.database:
                    for eid, mapping in poster.database.message_mapping.items():
                        if mapping.get('discord_message_id') == message.id:
                            entry_id = eid
                            category = mapping.get('category', 'unknown')
                            break
                
                if not entry_id:
                    entry_id = f"unknown_{discord_message_id}"
                    category = "unknown"
                
                # Add vote and get current count
                entry_data = {
                    'entry_id': entry_id,
                    'content': content,
                    'category': category,
                    'discord_channel_id': discord_channel_id,
                    'discord_message_id': int(discord_message_id)
                }
                
                vote_count, is_duplicate = poster.vote_tracker.add_vote(
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
                
                # Check if threshold reached
                if vote_count >= votes_required:
                    logger.info(f"Vote threshold reached ({vote_count}/{votes_required}) for message {discord_message_id}")
                    
                    # Get voter IDs
                    vote_data = poster.vote_tracker.get_votes(discord_message_id)
                    voter_ids = vote_data.get('voters', []) if vote_data else []
                    
                    # Delete the Discord message
                    try:
                        await message.delete()
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
                        if entry_id in poster.database.processed_ids:
                            del poster.database.processed_ids[entry_id]
                            poster.database._save_json(poster.database.processed_ids_path, poster.database.processed_ids)
                        
                        if entry_id in poster.database.message_mapping:
                            del poster.database.message_mapping[entry_id]
                            poster.database._save_json(poster.database.message_mapping_path, poster.database.message_mapping)
                        
                        # Also remove embedding if it exists
                        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
                        if content_hash in poster.database.embeddings:
                            del poster.database.embeddings[content_hash]
                            poster.database._save_json(poster.database.embeddings_path, poster.database.embeddings)
                        
                        logger.info(f"Removed entry {entry_id} from database")
                    except Exception as e:
                        logger.error(f"Error removing entry from database: {e}", exc_info=True)
                    
                    # Store in removed entries database
                    try:
                        poster.removed_entries_db.add_removed_entry(
                            entry_id=entry_id,
                            content=content,
                            category=category,
                            voter_ids=voter_ids,
                            discord_message_id=int(discord_message_id),
                            discord_channel_id=discord_channel_id
                        )
                        logger.info(f"Added entry {entry_id} to removed entries database")
                    except Exception as e:
                        logger.error(f"Error adding to removed entries: {e}", exc_info=True)
                    
                    # Clean up vote tracking
                    poster.vote_tracker.remove_tracking(discord_message_id)
                    
                    # Send confirmation
                    try:
                        await interaction.followup.send(
                            f"✅ Entry removed successfully after {vote_count} votes. This content will be used to improve future categorization.",
                            ephemeral=True
                        )
                    except:
                        pass  # Message might already be deleted
                    
                    logger.info(f"Successfully processed removal of entry {entry_id}")
                else:
                    # Not enough votes yet
                    await interaction.followup.send(
                        f"✅ Vote recorded ({vote_count}/{votes_required}). Need {votes_required - vote_count} more vote(s) to remove.",
                        ephemeral=True
                    )
                    
            except Exception as e:
                logger.error(f"Error in 'Not Valuable' command: {e}", exc_info=True)
                try:
                    if interaction.response.is_done():
                        await interaction.followup.send(
                            f"❌ An error occurred: {str(e)}",
                            ephemeral=True
                        )
                    else:
                        await interaction.response.send_message(
                            f"❌ An error occurred: {str(e)}",
                            ephemeral=True
                        )
                except:
                    pass
        
        # Manually add the Not Valuable command to the tree
        not_valuable_cmd = app_commands.ContextMenu(
            name="Not Valuable",
            callback=not_valuable
        )
        self.tree.add_command(not_valuable_cmd)
        logger.debug("Registered 'Not Valuable' context menu command")
        
        # Define the Re-categorize command function
        async def recategorize(interaction: discord.Interaction, message: discord.Message):
            """Context menu command to re-categorize a bot message"""
            logger.debug(f"'Re-categorize' command triggered by user {interaction.user.id} on message {message.id}")
            try:
                # Check if re-categorize is enabled
                enable_recategorize = getattr(config, 'RECATEGORIZE_COMMAND_ENABLED', True)
                if not enable_recategorize:
                    await interaction.response.send_message(
                        "❌ This feature is not enabled.",
                        ephemeral=True
                    )
                    return
                
                # Check if user is authorized
                allowed_user_ids = getattr(config, 'RECATEGORIZE_ALLOWED_USER_IDS', [])
                if interaction.user.id not in allowed_user_ids:
                    await interaction.response.send_message(
                        "❌ You don't have permission to use this command.",
                        ephemeral=True
                    )
                    logger.warning(f"Unauthorized re-categorize attempt by user {interaction.user.id}")
                    return
                
                # Check if message is from the bot
                if message.author != poster.client.user:
                    await interaction.response.send_message(
                        "❌ This command only works on messages posted by the bot.",
                        ephemeral=True
                    )
                    return
                
                # Find the entry in the database
                entry_id = None
                entry_data = None
                current_category = None
                
                if poster.database:
                    for eid, mapping in poster.database.message_mapping.items():
                        if mapping.get('discord_message_id') == message.id:
                            entry_id = eid
                            entry_data = mapping
                            current_category = mapping.get('category', 'unknown')
                            break
                
                if not entry_id or not entry_data:
                    await interaction.response.send_message(
                        "❌ Could not find entry data for this message in the database.",
                        ephemeral=True
                    )
                    return
                
                logger.info(
                    f"Re-categorize command from user {interaction.user.id}: "
                    f"Entry {entry_id} currently in {current_category}"
                )
                
                # Create a modal with a select dropdown for categories
                class RecategorizeModal(discord.ui.Modal, title="Re-categorize Entry"):
                    def __init__(self, current_cat, available_categories):
                        super().__init__()
                        self.current_category = current_cat
                        self.available_categories = available_categories
                        
                        # Create a text input for category (since Select can't be in Modal directly)
                        self.category_input = discord.ui.TextInput(
                            label="New Category",
                            placeholder=f"Currently: {current_cat}",
                            required=True,
                            max_length=50,
                            style=discord.TextStyle.short
                        )
                        self.add_item(self.category_input)
                    
                    async def on_submit(self, modal_interaction: discord.Interaction):
                        """Handle modal submission"""
                        new_category = self.category_input.value.strip().lower()
                        
                        # Validate the category
                        if new_category not in self.available_categories:
                            await modal_interaction.response.send_message(
                                f"❌ Invalid category: `{new_category}`\n"
                                f"Available categories: {', '.join(sorted(self.available_categories))}",
                                ephemeral=True
                            )
                            return
                        
                        # Check if it's the same category
                        if new_category == self.current_category:
                            await modal_interaction.response.send_message(
                                f"⚠️ This entry is already in the **{new_category}** category.",
                                ephemeral=True
                            )
                            return
                        
                        # Defer response for the recategorization process
                        await modal_interaction.response.defer(ephemeral=True)
                        
                        # Check if the message has a thread before re-categorizing
                        has_thread = message.thread is not None
                        
                        # Parse source type from entry_id (e.g., "twitter_123" -> "twitter")
                        source_type = entry_id.split('_')[0] if entry_id else None
                        
                        logger.info(
                            f"Re-categorizing entry {entry_id} from {self.current_category} to {new_category} (source_type: {source_type})"
                        )
                        
                        # Perform the re-categorization
                        try:
                            success, new_message_id, new_channel_id, error_msg = await poster.recategorize_entry(
                                message_id=message.id,
                                channel_id=message.channel.id,
                                new_category=new_category,
                                entry_id=entry_id,
                                content=entry_data.get('content', message.content),
                                media_files=None,  # Will download from original message
                                video_urls=entry_data.get('video_urls', []),
                                source_type=source_type
                            )
                            
                            if success:
                                success_msg = f"✅ Successfully re-categorized from **{self.current_category}** to **{new_category}**!\n"
                                success_msg += f"New message ID: {new_message_id}"
                                
                                # Add note about thread preservation if applicable
                                if has_thread:
                                    success_msg += "\n🧵 Thread with Perplexity content preserved!"
                                
                                await modal_interaction.followup.send(
                                    success_msg,
                                    ephemeral=True
                                )
                                logger.info(f"Successfully re-categorized entry {entry_id} to {new_category}")
                            else:
                                await modal_interaction.followup.send(
                                    f"❌ Failed to re-categorize: {error_msg}",
                                    ephemeral=True
                                )
                                logger.error(f"Failed to re-categorize entry {entry_id}: {error_msg}")
                        except Exception as e:
                            logger.error(f"Error during re-categorization: {e}", exc_info=True)
                            await modal_interaction.followup.send(
                                f"❌ An error occurred: {str(e)}",
                                ephemeral=True
                            )
                
                # Get available categories
                available_categories = list(config.DISCORD_CHANNELS.keys())
                
                # Show the modal
                modal = RecategorizeModal(current_category, available_categories)
                await interaction.response.send_modal(modal)
                
            except Exception as e:
                logger.error(f"Error in 'Re-categorize' command: {e}", exc_info=True)
                try:
                    if interaction.response.is_done():
                        await interaction.followup.send(
                            f"❌ An error occurred: {str(e)}",
                            ephemeral=True
                        )
                    else:
                        await interaction.response.send_message(
                            f"❌ An error occurred: {str(e)}",
                            ephemeral=True
                        )
                except:
                    pass
        
        # Manually add the Re-categorize command to the tree
        recategorize_cmd = app_commands.ContextMenu(
            name="Re-categorize",
            callback=recategorize
        )
        self.tree.add_command(recategorize_cmd)
        logger.debug("Registered 'Re-categorize' context menu command")
    
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
            
            # Send the message (no view/buttons needed - using context menu commands)
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
