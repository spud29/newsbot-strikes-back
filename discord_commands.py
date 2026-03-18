"""
Discord context menu command registration for the news aggregator bot
"""
import discord
from discord import app_commands
import asyncio
import re
import time
import requests
import config
from utils import logger
from discord_ui import RecategorizeModal, EditTextModal


def generate_thread_title(content):
    """
    Generate a concise thread title from content using Ollama

    Args:
        content: The content to summarize

    Returns:
        str: A short thread title (max 100 chars for Discord)
    """
    try:
        # Use Ollama to generate a very short summary for the thread title
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


def register_commands(poster):
    """
    Register all context menu commands on the poster's command tree.

    Args:
        poster: DiscordPoster instance (provides .tree, .client,
                .perplexity_client, .database, .vote_tracker, .removed_entries_db)
    """

    def extract_message_text(message: discord.Message) -> str:
        """Extract all readable text from a message, including rich embed fields."""
        parts = []

        if message.content:
            parts.append(message.content)

        for embed in message.embeds:
            if embed.author and embed.author.name:
                parts.append(embed.author.name)
            if embed.title:
                parts.append(embed.title)
            if embed.description:
                parts.append(embed.description)
            for field in embed.fields:
                if field.name:
                    parts.append(field.name)
                if field.value:
                    parts.append(field.value)
            if embed.footer and embed.footer.text:
                parts.append(embed.footer.text)

        return "\n".join(parts).strip()

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

            content = extract_message_text(message)
            if not content:
                await interaction.followup.send(
                    "❌ No readable text found in this message.",
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
                thread_name = await asyncio.to_thread(generate_thread_title, content)
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
    poster.tree.add_command(get_more_info_cmd)
    logger.debug("Registered 'Get More Info' context menu command")

    # NOTE: "Not Valuable" context menu command was removed to stay within
    # Discord's 5 message context menu command limit. Edit Text took its slot.

    # Define the Re-categorize command function
    async def recategorize(interaction: discord.Interaction, message: discord.Message):
        """Context menu command to re-categorize a bot message"""
        logger.debug(f"'Re-categorize' command triggered by user {interaction.user.id} on message {message.id}")
        try:
            # FAST CHECKS FIRST - these don't require database lookups
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

            # Find the entry in the database using optimized reverse lookup
            entry_id = None
            entry_data = None
            current_category = None

            if poster.database:
                # Use reverse index if available, otherwise fall back to iteration
                entry_id = poster.database.get_entry_id_by_discord_message(message.id)
                if entry_id:
                    entry_data = poster.database.get_discord_message_info(entry_id)
                    current_category = entry_data.get('category', 'unknown') if entry_data else 'unknown'

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

            # Get available categories
            available_categories = list(config.DISCORD_CHANNELS.keys())

            # Show the modal using the external RecategorizeModal class
            modal = RecategorizeModal(current_category, available_categories, entry_id, entry_data, message, poster)
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
    poster.tree.add_command(recategorize_cmd)
    logger.debug("Registered 'Re-categorize' context menu command")

    # Define the Source command function
    async def source(interaction: discord.Interaction, message: discord.Message):
        """Context menu command to show the original Telegram/Twitter source URL"""
        logger.debug(f"'Source' command triggered by user {interaction.user.id} on message {message.id}")
        try:
            # Check if Source command is enabled
            if not getattr(config, 'SOURCE_COMMAND_ENABLED', True):
                await interaction.response.send_message(
                    "❌ This feature is not enabled.",
                    ephemeral=True
                )
                return

            # Check if message is from the bot
            if message.author != poster.client.user:
                await interaction.response.send_message(
                    "❌ This command only works on messages posted by the bot.",
                    ephemeral=True
                )
                return

            # Look up the entry in the database
            entry_id = None
            entry_data = None

            if poster.database:
                entry_id = poster.database.get_entry_id_by_discord_message(message.id)
                if entry_id:
                    entry_data = poster.database.get_discord_message_info(entry_id)

            if not entry_id or not entry_data:
                await interaction.response.send_message(
                    "❌ Could not find entry data for this message in the database.",
                    ephemeral=True
                )
                return

            source_url = entry_data.get('source_url')

            if not source_url:
                await interaction.response.send_message(
                    "❌ No source URL found for this entry.",
                    ephemeral=True
                )
                return

            # Send the source URL wrapped in angle brackets to suppress Discord embeds
            await interaction.response.send_message(
                f"<{source_url}>",
                ephemeral=True
            )
            logger.info(f"'Source' shown for entry {entry_id}: {source_url}")

        except Exception as e:
            logger.error(f"Error in 'Source' command: {e}", exc_info=True)
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

    # Manually add the Source command to the tree
    source_cmd = app_commands.ContextMenu(
        name="Source",
        callback=source
    )
    poster.tree.add_command(source_cmd)
    logger.debug("Registered 'Source' context menu command")

    # Define the Edit Text command function
    async def edit_text(interaction: discord.Interaction, message: discord.Message):
        """Context menu command to edit the text of a bot message (admin only)"""
        logger.debug(f"'Edit Text' command triggered by user {interaction.user.id} on message {message.id}")
        try:
            # Check if edit text is enabled
            if not getattr(config, 'EDIT_TEXT_COMMAND_ENABLED', True):
                await interaction.response.send_message(
                    "❌ This feature is not enabled.",
                    ephemeral=True
                )
                return

            # Check if user is authorized (same list as Re-categorize)
            allowed_user_ids = getattr(config, 'RECATEGORIZE_ALLOWED_USER_IDS', [])
            if interaction.user.id not in allowed_user_ids:
                await interaction.response.send_message(
                    "❌ You don't have permission to use this command.",
                    ephemeral=True
                )
                logger.warning(f"Unauthorized edit text attempt by user {interaction.user.id}")
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

            if poster.database:
                entry_id = poster.database.get_entry_id_by_discord_message(message.id)
                if entry_id:
                    entry_data = poster.database.get_discord_message_info(entry_id)

            if not entry_id or not entry_data:
                await interaction.response.send_message(
                    "❌ Could not find entry data for this message in the database.",
                    ephemeral=True
                )
                return

            logger.info(f"Edit Text command from user {interaction.user.id}: Entry {entry_id}")

            # Show the modal
            modal = EditTextModal(entry_id, entry_data, message, poster)
            await interaction.response.send_modal(modal)

        except Exception as e:
            logger.error(f"Error in 'Edit Text' command: {e}", exc_info=True)
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

    # Manually add the Edit Text command to the tree
    edit_text_cmd = app_commands.ContextMenu(
        name="Edit Text",
        callback=edit_text
    )
    poster.tree.add_command(edit_text_cmd)
    logger.debug("Registered 'Edit Text' context menu command")

    # Define the Reprocess Entry command function
    async def reprocess_entry(interaction: discord.Interaction, message: discord.Message):
        """Context menu command to re-run the full pipeline on an already-posted bot message (admin only)"""
        logger.debug(f"'Reprocess Entry' triggered by user {interaction.user.id} on message {message.id}")
        try:
            if not getattr(config, 'REPROCESS_COMMAND_ENABLED', True):
                await interaction.response.send_message("❌ This feature is not enabled.", ephemeral=True)
                return

            allowed_user_ids = getattr(config, 'RECATEGORIZE_ALLOWED_USER_IDS', [])
            if interaction.user.id not in allowed_user_ids:
                await interaction.response.send_message(
                    "❌ You don't have permission to use this command.", ephemeral=True
                )
                logger.warning(f"Unauthorized reprocess attempt by user {interaction.user.id}")
                return

            if message.author != poster.client.user:
                await interaction.response.send_message(
                    "❌ This command only works on messages posted by the bot.", ephemeral=True
                )
                return

            if not poster.reprocess_callback:
                await interaction.response.send_message(
                    "❌ Reprocess is not available (callback not registered).", ephemeral=True
                )
                return

            entry_id = poster.database.get_entry_id_by_discord_message(message.id) if poster.database else None
            if not entry_id:
                await interaction.response.send_message(
                    "❌ Could not find entry data for this message.", ephemeral=True
                )
                return

            entry_data = poster.database.get_discord_message_info(entry_id)
            if not entry_data:
                await interaction.response.send_message(
                    "❌ Could not retrieve entry details.", ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"⚙️ Reprocessing `{entry_id}`... A new post will appear shortly.",
                ephemeral=True
            )

            original_channel_id = message.channel.id
            original_message_id = message.id

            async def run_reprocess():
                try:
                    await poster.reprocess_callback(entry_id, entry_data, original_channel_id, original_message_id)
                except Exception as e:
                    logger.error(f"Background reprocess task failed for {entry_id}: {e}", exc_info=True)

            asyncio.create_task(run_reprocess())

        except Exception as e:
            logger.error(f"Error in 'Reprocess Entry' command: {e}", exc_info=True)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❌ An error occurred: {str(e)}", ephemeral=True)
            except:
                pass

    reprocess_entry_cmd = app_commands.ContextMenu(name="Reprocess Entry", callback=reprocess_entry)
    poster.tree.add_command(reprocess_entry_cmd)
    logger.debug("Registered 'Reprocess Entry' context menu command")

    # --- /news slash command ---
    # Per-user cooldown tracking
    _news_cooldowns: dict = {}

    @poster.tree.command(name="news", description="Search for the latest news on any topic")
    @app_commands.describe(topic="The topic to search for (e.g. Bitcoin ETF, AI regulation, Nintendo Switch 2)")
    async def news_slash(interaction: discord.Interaction, topic: str):
        """Slash command to search for news on any topic via Perplexity AI"""
        logger.debug(f"'/news' command triggered by user {interaction.user.id} with topic: {topic}")
        try:
            if not getattr(config, 'NEWS_SEARCH_COMMAND_ENABLED', True):
                await interaction.response.send_message(
                    "❌ This command is not currently enabled.",
                    ephemeral=True
                )
                return

            # Defer immediately to avoid 3s timeout
            await interaction.response.defer(ephemeral=True)

            # Cooldown check
            cooldown_seconds = getattr(config, 'NEWS_SEARCH_COOLDOWN_SECONDS', 30)
            now = time.time()
            remaining = cooldown_seconds - (now - _news_cooldowns.get(interaction.user.id, 0))
            if remaining > 0:
                await interaction.followup.send(
                    f"Please wait {remaining:.0f} seconds before searching again.",
                    ephemeral=True
                )
                return

            topic = topic.strip()
            if len(topic) < 2:
                await interaction.followup.send(
                    "Please provide a longer topic (at least 2 characters).",
                    ephemeral=True
                )
                return

            if not poster.perplexity_client or not poster.perplexity_client.is_available():
                await interaction.followup.send(
                    "Perplexity search is not available. API key may not be configured.",
                    ephemeral=True
                )
                return

            # Record cooldown before the slow API call
            _news_cooldowns[interaction.user.id] = now

            result = await asyncio.to_thread(poster.perplexity_client.news_search, topic)

            if result['success'] and result.get('answer'):
                answer = result['answer']
                citations = result.get('citations', [])

                # Truncate if needed (embed description limit is 4096)
                max_length = 3900
                truncated = False
                if len(answer) > max_length:
                    answer = answer[:max_length] + "..."
                    truncated = True

                embed = discord.Embed(
                    title=f"News: {topic[:200]}",
                    description=answer,
                    color=discord.Color.blue(),
                    timestamp=discord.utils.utcnow()
                )

                # Add citations as a field (up to 10 sources)
                if citations:
                    sources_text = ""
                    for i, citation in enumerate(citations[:10], 1):
                        if isinstance(citation, dict):
                            url = citation.get('url', citation.get('link', ''))
                            cite_title = citation.get('title', citation.get('name', citation.get('domain', 'Source')))
                            if url:
                                sources_text += f"{i}. [{cite_title}]({url})\n"
                            else:
                                sources_text += f"{i}. {cite_title}\n"
                        elif isinstance(citation, str):
                            sources_text += f"{i}. {citation}\n"

                    if sources_text:
                        if len(sources_text) > 1024:
                            sources_text = sources_text[:1021] + "..."
                        embed.add_field(name="Sources", value=sources_text, inline=False)

                footer_text = f"Requested by {interaction.user.display_name}"
                if truncated:
                    footer_text += " | Response truncated"
                embed.set_footer(text=footer_text)

                # Post to channel as a persistent (non-ephemeral) message
                posted_msg = await interaction.channel.send(embed=embed)

                await interaction.followup.send("✅ Results posted!", ephemeral=True)
                logger.info(f"News search for '{topic}' completed ({len(answer)} chars, {len(citations)} citations)")

            else:
                error_msg = result.get('error', 'Unknown error')
                await interaction.followup.send(f"Search failed: {error_msg}", ephemeral=True)
                logger.error(f"News search failed for '{topic}': {error_msg}")

        except Exception as e:
            logger.error(f"Error in '/news' command: {e}", exc_info=True)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❌ An error occurred: {str(e)}", ephemeral=True)
            except:
                pass

    logger.debug("Registered '/news' slash command")
