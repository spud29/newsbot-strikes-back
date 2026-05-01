"""
Discord context menu command registration for the news aggregator bot
"""
import discord
from discord import app_commands
import asyncio
import json
import time
import config
from utils import logger
from discord_ui import RecategorizeView, SetCategoryView, EditTextModal



def _build_entry_info_embed(entry_id, entry_data):
    """Build a Discord embed showing full metadata for an entry."""
    import datetime

    category = entry_data.get('category', 'unknown')
    original_category = entry_data.get('original_category')
    placement_reason = entry_data.get('placement_reason') or 'Not recorded (legacy entry)'
    reasoning = entry_data.get('reasoning') or 'None'
    source_url = entry_data.get('source_url') or 'None'
    source_type = entry_data.get('source_type') or 'unknown'
    user_edited = bool(entry_data.get('user_edited'))
    timestamp = entry_data.get('timestamp')

    recategorized = original_category and original_category != category
    embed = discord.Embed(
        title="Entry Info",
        color=discord.Color.orange() if recategorized else discord.Color.blurple()
    )

    embed.add_field(name="Entry ID", value=f"`{entry_id}`", inline=False)
    embed.add_field(name="Current Category", value=category, inline=True)
    if recategorized:
        embed.add_field(name="Original Category", value=original_category, inline=True)
    embed.add_field(name="Source Type", value=source_type, inline=True)

    if len(placement_reason) > 1020:
        placement_reason = placement_reason[:1020] + "..."
    embed.add_field(name="Why It's Here", value=placement_reason, inline=False)

    # Show only the AI's categorization rationale, not the filter override details
    # (those are already covered by "Why It's Here")
    import re
    display_reasoning = reasoning
    if " | OVERRIDDEN:" in reasoning:
        ai_part = reasoning.split(" | OVERRIDDEN:")[0]
        ai_part = re.sub(r"^AI suggested '[^']+': ", "", ai_part).strip()
        display_reasoning = ai_part or reasoning
    if len(display_reasoning) > 1020:
        display_reasoning = display_reasoning[:1020] + "..."
    embed.add_field(name="AI Reasoning", value=display_reasoning, inline=False)

    embed.add_field(name="Source URL", value=source_url if source_url != 'None' else 'None', inline=False)

    flags = []
    if recategorized:
        flags.append("Re-categorized")
    if user_edited:
        flags.append("Text edited")
    embed.add_field(name="Flags", value=", ".join(flags) if flags else "None", inline=True)

    if timestamp:
        dt = datetime.datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M UTC")
        embed.add_field(name="Processed At", value=dt, inline=True)

    return embed


def register_commands(poster):
    """
    Register all context menu commands on the poster's command tree.

    Args:
        poster: DiscordPoster instance (provides .tree, .client,
                .perplexity_client, .database, .removed_entries_db)
    """

    # Define the Entry Info command function
    async def entry_info(interaction: discord.Interaction, message: discord.Message):
        """Context menu command to show full entry metadata for a bot message"""
        logger.debug(f"'Entry Info' command triggered by user {interaction.user.id} on message {message.id}")
        try:
            if message.author != poster.client.user:
                await interaction.response.send_message(
                    "❌ This command only works on messages posted by the bot.",
                    ephemeral=True
                )
                return

            entry_id = None
            entry_data = None

            if poster.database:
                entry_id = poster.database.get_entry_id_by_discord_message(message.id)
                if entry_id:
                    entry_data = poster.database.get_discord_message_info(entry_id)

            if not entry_id or not entry_data:
                await interaction.response.send_message(
                    "❌ No database record found for this message.",
                    ephemeral=True
                )
                return

            embed = _build_entry_info_embed(entry_id, entry_data)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"'Entry Info' shown for entry {entry_id} to user {interaction.user.id}")

        except Exception as e:
            logger.error(f"Error in 'Entry Info' command: {e}", exc_info=True)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❌ An error occurred: {str(e)}", ephemeral=True)
            except:
                pass

    entry_info_cmd = app_commands.ContextMenu(
        name="Entry Info",
        callback=entry_info
    )
    poster.tree.add_command(entry_info_cmd)
    logger.debug("Registered 'Entry Info' context menu command")

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

            # Get available categories (only ignore and unified channel for simplified routing)
            available_categories = [config.DEFAULT_CATEGORY, "__unified__"]

            # Show an ephemeral dropdown so the user can pick a channel
            view = RecategorizeView(current_category, available_categories, entry_id, entry_data, message, poster)
            await interaction.response.send_message(
                f"Route this entry (currently **{current_category}**):",
                view=view,
                ephemeral=True
            )

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

    # Manually add the Move to Channel command to the tree
    recategorize_cmd = app_commands.ContextMenu(
        name="Move to Channel",
        callback=recategorize
    )
    poster.tree.add_command(recategorize_cmd)
    logger.debug("Registered 'Move to Channel' context menu command")

    # Define the Re-categorize (label-only) command function
    async def recategorize_label(interaction: discord.Interaction, message: discord.Message):
        """Context menu command to re-categorize an entry and update its category tag"""
        logger.debug(f"'Re-categorize' (label) command triggered by user {interaction.user.id} on message {message.id}")
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
                f"Re-categorize (label) command from user {interaction.user.id}: "
                f"Entry {entry_id} currently in {current_category}"
            )

            # Get available categories (all except ignore, and excluding current category)
            available_categories = [cat for cat in config.DISCORD_CHANNELS.keys() if cat != config.DEFAULT_CATEGORY]

            # Show an ephemeral dropdown so the user can pick a category
            view = SetCategoryView(current_category, available_categories, entry_id, entry_data, message, poster)
            await interaction.response.send_message(
                f"Select a new category for this entry (currently **{current_category}**):",
                view=view,
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error in 'Re-categorize' (label) command: {e}", exc_info=True)
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

    # Manually add the Re-categorize (label-only) command to the tree
    recategorize_label_cmd = app_commands.ContextMenu(
        name="Re-categorize",
        callback=recategorize_label
    )
    poster.tree.add_command(recategorize_label_cmd)
    logger.debug("Registered 'Re-categorize' (label-only) context menu command")

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



    # Define the Restore Original command function
    async def restore_original(interaction: discord.Interaction, message: discord.Message):
        """Context menu command to reverse a supersede and restore the original entry"""
        logger.debug(f"'Restore Original' triggered by user {interaction.user.id} on message {message.id}")
        try:
            if not getattr(config, 'SUPERSEDE_ENABLED', False):
                await interaction.response.send_message(
                    "❌ Supersede is not enabled.", ephemeral=True
                )
                return

            allowed_user_ids = getattr(config, 'RECATEGORIZE_ALLOWED_USER_IDS', [])
            if interaction.user.id not in allowed_user_ids:
                await interaction.response.send_message(
                    "❌ You don't have permission to use this command.", ephemeral=True
                )
                logger.warning(f"Unauthorized 'Restore Original' attempt by user {interaction.user.id}")
                return

            if message.author != poster.client.user:
                await interaction.response.send_message(
                    "❌ This command only works on messages posted by the bot.", ephemeral=True
                )
                return

            if not poster.database:
                await interaction.response.send_message(
                    "❌ Database unavailable.", ephemeral=True
                )
                return

            # PRIMARY LOOKUP: user right-clicked the superseder in the main channel
            superseder_entry_id = poster.database.get_entry_id_by_discord_message(message.id)
            clicked_original_row = None

            if not superseder_entry_id:
                # SECONDARY LOOKUP: user right-clicked the archived copy in the superseded channel
                clicked_original_row = poster.database.get_entry_by_superseded_channel_message(message.id)
                if clicked_original_row:
                    superseder_entry_id = clicked_original_row.get('superseded_by')

            if not superseder_entry_id:
                await interaction.response.send_message(
                    "❌ No database record found for this message.", ephemeral=True
                )
                return

            # Resolve the original entry
            if clicked_original_row:
                original = clicked_original_row
            else:
                original = poster.database.get_entry_superseded_by(superseder_entry_id)
                if not original:
                    await interaction.response.send_message(
                        "ℹ️ This message has not superseded any entry.", ephemeral=True
                    )
                    return

            await interaction.response.defer(ephemeral=True)

            original_entry_id    = original['entry_id']
            original_content     = original['content'] or ''
            original_category    = original['category'] or config.DEFAULT_CATEGORY
            original_source_type = original['source_type']
            original_source_url  = original.get('source_url')
            original_video_urls  = json.loads(original['video_urls']) if original.get('video_urls') else []
            original_telegram_id = original.get('telegram_message_id')

            # Re-download media from the source so images are restored too
            media_files = []
            if poster.media_handler:
                try:
                    media_files, redownloaded_video_urls = await poster.media_handler.redownload_media(
                        entry_id=original_entry_id,
                        source_type=original_source_type,
                        source_url=original_source_url,
                        telegram_message_id=original_telegram_id,
                    )
                    if redownloaded_video_urls:
                        original_video_urls = redownloaded_video_urls
                    if media_files:
                        logger.info(f"Re-downloaded {len(media_files)} file(s) for restore of {original_entry_id}")
                except Exception as media_err:
                    logger.warning(f"Media re-download failed for restore of {original_entry_id}: {media_err}")

            # Re-post the original content to Discord
            success, new_msg_id, new_channel_id = await poster.post_message(
                category=original_category,
                content=original_content,
                media_files=media_files or None,
                video_urls=original_video_urls,
                source_type=original_source_type,
                entry_id=original_entry_id,
            )

            if not success or not new_msg_id:
                await interaction.followup.send(
                    "❌ Failed to re-post the original entry. No changes made.", ephemeral=True
                )
                return

            # Delete the superseder Discord message (and stale archive if restoring from superseded channel)
            if clicked_original_row:
                # Flow B: clicked the archive — delete the superseder from the main channel
                superseder_mapping = poster.database.get_discord_message_info(superseder_entry_id)
                if superseder_mapping and superseder_mapping.get('discord_message_id'):
                    try:
                        sup_channel = poster.client.get_channel(superseder_mapping['discord_channel_id'])
                        if sup_channel:
                            sup_msg = await sup_channel.fetch_message(superseder_mapping['discord_message_id'])
                            await sup_msg.delete()
                    except Exception as del_err:
                        logger.warning(f"Could not delete superseder message {superseder_mapping.get('discord_message_id')}: {del_err}")
                # Also remove the now-stale archive message
                try:
                    await message.delete()
                except Exception as del_err:
                    logger.warning(f"Could not delete archived superseded message {message.id}: {del_err}")
            else:
                # Flow A: clicked the superseder directly
                try:
                    await message.delete()
                except Exception as del_err:
                    logger.warning(f"Could not delete superseder message {message.id}: {del_err}")

            # Restore the original entry in the DB (clear superseded_by, update message ID)
            poster.database.restore_superseded_entry(original_entry_id, new_msg_id, new_channel_id)

            # Re-apply stored emoji reactions to the freshly re-posted message
            try:
                restored_channel = poster.client.get_channel(new_channel_id)
                if restored_channel:
                    restored_msg = await restored_channel.fetch_message(new_msg_id)
                    await poster.restore_reactions(original_entry_id, restored_msg)
            except Exception as react_err:
                logger.warning(f"Could not restore reactions for {original_entry_id}: {react_err}")

            # Clean up the superseder from message_mapping and embeddings
            # (keep processed_entries so it isn't re-fetched)
            try:
                poster.database.delete_message_mapping(superseder_entry_id)
                poster.database.delete_embedding_by_entry_id(superseder_entry_id)
            except Exception as cleanup_err:
                logger.warning(f"Partial cleanup failure for superseder {superseder_entry_id}: {cleanup_err}")

            preview = original_content[:80] + ('...' if len(original_content) > 80 else '')
            media_note = f" ({len(media_files)} file(s) re-attached)" if media_files else " (no media)"
            await interaction.followup.send(
                f"✅ Original entry restored to **{original_category}**{media_note}.\n"
                f"Preview: *{preview}*",
                ephemeral=True
            )
            logger.info(
                f"User {interaction.user.id} restored {original_entry_id} "
                f"(superseded by {superseder_entry_id})"
            )

        except Exception as e:
            logger.error(f"Error in 'Restore Original' command: {e}", exc_info=True)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❌ An error occurred: {str(e)}", ephemeral=True)
            except:
                pass

    restore_original_cmd = app_commands.ContextMenu(name="Restore Original", callback=restore_original)
    poster.tree.add_command(restore_original_cmd)
    logger.debug("Registered 'Restore Original' context menu command")

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
