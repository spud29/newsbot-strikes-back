"""
Discord UI components (modals, views) for the news aggregator bot
"""
import discord
import asyncio
import time
import config
from utils import logger


class RecategorizeModal(discord.ui.Modal, title="Re-categorize Entry"):
    """Modal for re-categorizing an entry to a different category"""

    def __init__(self, current_cat, available_categories, entry_id, entry_data, message, poster):
        super().__init__()
        self.current_category = current_cat
        self.available_categories = available_categories
        self.entry_id = entry_id
        self.entry_data = entry_data
        self.message = message
        self.poster = poster

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
        try:
            # Log immediately to confirm callback is reached
            logger.debug(f"RecategorizeModal on_submit called for entry {self.entry_id}")

            new_category = self.category_input.value.strip().lower()
            logger.debug(f"New category input: '{new_category}', available: {self.available_categories}")

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

            # Defer response IMMEDIATELY to avoid timeout (Discord gives 3 seconds)
            logger.debug("Deferring modal interaction response...")
            await modal_interaction.response.defer(ephemeral=True)
            logger.debug("Modal interaction deferred successfully")

            # Check if the message has a thread before re-categorizing
            has_thread = self.message.thread is not None

            # Parse source type from entry_id (e.g., "twitter_123" -> "twitter")
            source_type = self.entry_id.split('_')[0] if self.entry_id else None

            logger.info(
                f"Re-categorizing entry {self.entry_id} from {self.current_category} to {new_category} (source_type: {source_type})"
            )

            # Perform the re-categorization
            success, new_message_id, new_channel_id, error_msg = await self.poster.recategorize_entry(
                message_id=self.message.id,
                channel_id=self.message.channel.id,
                new_category=new_category,
                entry_id=self.entry_id,
                content=self.entry_data.get('content', self.message.content),
                media_files=None,  # Will download from original message
                video_urls=self.entry_data.get('video_urls', []),
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
                logger.info(f"Successfully re-categorized entry {self.entry_id} to {new_category}")
            else:
                await modal_interaction.followup.send(
                    f"❌ Failed to re-categorize: {error_msg}",
                    ephemeral=True
                )
                logger.error(f"Failed to re-categorize entry {self.entry_id}: {error_msg}")

        except Exception as e:
            logger.error(f"Error in RecategorizeModal on_submit: {e}", exc_info=True)
            # Try to send error response
            try:
                if modal_interaction.response.is_done():
                    await modal_interaction.followup.send(
                        f"❌ An error occurred: {str(e)}",
                        ephemeral=True
                    )
                else:
                    await modal_interaction.response.send_message(
                        f"❌ An error occurred: {str(e)}",
                        ephemeral=True
                    )
            except Exception as followup_error:
                logger.error(f"Failed to send error followup: {followup_error}")

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        """Handle errors in the modal"""
        logger.error(f"RecategorizeModal error: {error}", exc_info=True)
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
            logger.error(f"Failed to send modal error message: {e}")


class EditTextModal(discord.ui.Modal, title="Edit Entry Text"):
    """Modal for editing the text content of a bot message"""

    def __init__(self, entry_id, entry_data, message, poster):
        super().__init__()
        self.entry_id = entry_id
        self.entry_data = entry_data
        self.message = message
        self.poster = poster

        current_content = entry_data.get('content', message.content) or ''

        # Text input pre-filled with current content
        self.text_input = discord.ui.TextInput(
            label="Message Text",
            default=current_content[:4000],  # Discord modal limit is 4000 chars
            required=True,
            max_length=4000,
            style=discord.TextStyle.long
        )
        self.add_item(self.text_input)

    async def on_submit(self, modal_interaction: discord.Interaction):
        """Handle modal submission"""
        try:
            logger.debug(f"EditTextModal on_submit called for entry {self.entry_id}")

            new_text = self.text_input.value.strip()
            if not new_text:
                await modal_interaction.response.send_message(
                    "❌ Text cannot be empty.",
                    ephemeral=True
                )
                return

            # Defer response to avoid timeout
            await modal_interaction.response.defer(ephemeral=True)

            # Edit the Discord message
            try:
                await self.message.edit(content=new_text, suppress=True)
                logger.info(f"Edited Discord message {self.message.id} for entry {self.entry_id}")
            except discord.Forbidden:
                await modal_interaction.followup.send(
                    "❌ Bot lacks permission to edit this message.",
                    ephemeral=True
                )
                return
            except discord.HTTPException as e:
                await modal_interaction.followup.send(
                    f"❌ Failed to edit message: {str(e)}",
                    ephemeral=True
                )
                return

            # Update the database
            try:
                if self.poster.database:
                    self.poster.database.update_message_mapping_fields(
                        self.entry_id, content=new_text, user_edited=1
                    )
                    logger.info(f"Updated database content for entry {self.entry_id}")
            except Exception as e:
                logger.error(f"Failed to update database for entry {self.entry_id}: {e}")
                # Message was already edited, so warn but don't fail
                await modal_interaction.followup.send(
                    f"⚠️ Message edited but database update failed: {str(e)}",
                    ephemeral=True
                )
                return

            await modal_interaction.followup.send(
                "✅ Entry text updated successfully.",
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error in EditTextModal on_submit: {e}", exc_info=True)
            try:
                if modal_interaction.response.is_done():
                    await modal_interaction.followup.send(
                        f"❌ An error occurred: {str(e)}",
                        ephemeral=True
                    )
                else:
                    await modal_interaction.response.send_message(
                        f"❌ An error occurred: {str(e)}",
                        ephemeral=True
                    )
            except Exception as followup_error:
                logger.error(f"Failed to send error followup: {followup_error}")

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        """Handle errors in the modal"""
        logger.error(f"EditTextModal error: {error}", exc_info=True)
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
            logger.error(f"Failed to send modal error message: {e}")


# Per-user cooldown tracking for news search
_news_cooldowns = {}


class NewsSearchModal(discord.ui.Modal, title="News Search"):
    """Modal for searching news on any topic via Perplexity AI"""

    def __init__(self, poster, channel=None):
        super().__init__()
        self.poster = poster
        self.channel = channel

        self.topic_input = discord.ui.TextInput(
            label="Topic",
            placeholder="e.g. Bitcoin ETF, AI regulation, Nintendo Switch 2",
            required=True,
            max_length=200,
            style=discord.TextStyle.short
        )
        self.add_item(self.topic_input)

    async def on_submit(self, modal_interaction: discord.Interaction):
        """Handle modal submission"""
        try:
            topic = self.topic_input.value.strip()
            logger.debug(f"NewsSearchModal on_submit called for topic: {topic}")

            if len(topic) < 2:
                await modal_interaction.response.send_message(
                    "Please provide a longer search topic (at least 2 characters).",
                    ephemeral=True
                )
                return

            # Cooldown check
            cooldown_seconds = getattr(config, 'NEWS_SEARCH_COOLDOWN_SECONDS', 30)
            user_id = modal_interaction.user.id
            now = time.time()
            last_use = _news_cooldowns.get(user_id, 0)
            remaining = cooldown_seconds - (now - last_use)

            if remaining > 0:
                await modal_interaction.response.send_message(
                    f"Please wait {remaining:.0f} seconds before searching again.",
                    ephemeral=True
                )
                return

            # Check if Perplexity is available
            if not self.poster.perplexity_client or not self.poster.perplexity_client.is_available():
                await modal_interaction.response.send_message(
                    "Perplexity search is not available. API key may not be configured.",
                    ephemeral=True
                )
                return

            # Send immediate loading indicator
            await modal_interaction.response.send_message(
                f"🔍 Searching for news on **{topic}**...",
                ephemeral=True
            )

            # Record cooldown after responding
            _news_cooldowns[user_id] = now

            # Perform the search
            result = await asyncio.to_thread(self.poster.perplexity_client.news_search, topic)

            if result['success'] and result.get('answer'):
                answer = result['answer']
                citations = result.get('citations', [])

                # Truncate if needed (embed description limit is 4096)
                max_length = 3900
                truncated = False
                if len(answer) > max_length:
                    answer = answer[:max_length] + "..."
                    truncated = True

                # Build the embed
                embed = discord.Embed(
                    title=f"News: {topic[:200]}",
                    description=answer,
                    color=discord.Color.blue(),
                    timestamp=discord.utils.utcnow()
                )

                # Add citations as a field (up to 10 sources)
                if citations:
                    sources_text = ""
                    display_citations = citations[:10]

                    for i, citation in enumerate(display_citations, 1):
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
                        embed.add_field(
                            name="Sources",
                            value=sources_text,
                            inline=False
                        )

                footer_text = f"Requested by {modal_interaction.user.display_name}"
                if truncated:
                    footer_text += " | Response truncated"
                embed.set_footer(text=footer_text)

                # Post to channel as a persistent (non-ephemeral) message
                channel = self.channel or modal_interaction.channel
                posted_msg = await channel.send(embed=embed)

                # Create a thread on it for discussion
                thread_name = f"News: {topic}"[:100]
                try:
                    await posted_msg.create_thread(
                        name=thread_name,
                        auto_archive_duration=1440
                    )
                except discord.Forbidden:
                    logger.warning("No permission to create thread on news search result")
                except discord.HTTPException as thread_err:
                    logger.warning(f"Failed to create thread: {thread_err}")

                # Update the ephemeral loading message to confirm
                await modal_interaction.edit_original_response(
                    content="✅ Results posted!"
                )
                logger.info(f"News search for '{topic}' completed ({len(answer)} chars, {len(citations)} citations)")

            else:
                error_msg = result.get('error', 'Unknown error')
                await modal_interaction.edit_original_response(
                    content=f"Search failed: {error_msg}"
                )
                logger.error(f"News search failed for '{topic}': {error_msg}")

        except Exception as e:
            logger.error(f"Error in NewsSearchModal on_submit: {e}", exc_info=True)
            try:
                if modal_interaction.response.is_done():
                    await modal_interaction.edit_original_response(
                        content=f"An error occurred: {str(e)}"
                    )
                else:
                    await modal_interaction.response.send_message(
                        f"An error occurred: {str(e)}",
                        ephemeral=True
                    )
            except Exception as followup_error:
                logger.error(f"Failed to send error response: {followup_error}")

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        """Handle errors in the modal"""
        logger.error(f"NewsSearchModal error: {error}", exc_info=True)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    f"An error occurred: {str(error)}",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"An error occurred: {str(error)}",
                    ephemeral=True
                )
        except Exception as e:
            logger.error(f"Failed to send modal error message: {e}")
