"""
Discord context menu command registration for the news aggregator bot
"""
import discord
from discord import app_commands
import asyncio
import re
import requests
import config
from utils import logger
from discord_ui import RecategorizeModal


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
                entry_id = poster.database.get_entry_id_by_discord_message(message.id)
                if entry_id:
                    mapping_info = poster.database.get_discord_message_info(entry_id)
                    category = mapping_info.get('category', 'unknown') if mapping_info else 'unknown'

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
                    poster.database.delete_processed(entry_id)
                    poster.database.delete_message_mapping(entry_id)
                    poster.database.delete_embedding_by_content(content)

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
    poster.tree.add_command(not_valuable_cmd)
    logger.debug("Registered 'Not Valuable' context menu command")

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

    # Define the Why This Category command function
    async def why_this_category(interaction: discord.Interaction, message: discord.Message):
        """Context menu command to see why a message was categorized the way it was"""
        logger.debug(f"'Why This Category?' command triggered by user {interaction.user.id} on message {message.id}")
        try:
            # Check if user is authorized (same restriction as Re-categorize)
            allowed_user_ids = getattr(config, 'RECATEGORIZE_ALLOWED_USER_IDS', [])
            if interaction.user.id not in allowed_user_ids:
                await interaction.response.send_message(
                    "❌ You don't have permission to use this command.",
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

            category = entry_data.get('category', 'unknown')
            reasoning = entry_data.get('reasoning')
            source_type = entry_data.get('source_type', 'unknown')

            # Build the response
            response_lines = [
                f"📂 **Category:** {category}",
                f"💬 **Reasoning:** {reasoning if reasoning else 'No reasoning stored (entry was processed before this feature was added)'}",
                f"🔗 **Source type:** {source_type}",
                f"🆔 **Entry ID:** `{entry_id}`",
            ]

            await interaction.response.send_message(
                "\n".join(response_lines),
                ephemeral=True
            )
            logger.info(f"'Why This Category?' shown for entry {entry_id}: {category} - {reasoning}")

        except Exception as e:
            logger.error(f"Error in 'Why This Category?' command: {e}", exc_info=True)
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

    # Manually add the Why This Category command to the tree
    why_category_cmd = app_commands.ContextMenu(
        name="Why This Category?",
        callback=why_this_category
    )
    poster.tree.add_command(why_category_cmd)
    logger.debug("Registered 'Why This Category?' context menu command")
