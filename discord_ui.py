"""
Discord UI components (modals, views) for the news aggregator bot
"""
import discord
import asyncio
import config
from utils import logger, ensure_url_on_own_line


class RecategorizeView(discord.ui.View):
    """Ephemeral view with a Select dropdown for re-categorizing an entry"""

    def __init__(self, current_cat, available_categories, entry_id, entry_data, message, poster):
        super().__init__(timeout=60)
        self.current_category = current_cat
        self.entry_id = entry_id
        self.entry_data = entry_data
        self.message = message
        self.poster = poster

        # Build select options — all categories except the current one
        options = []
        for cat in sorted(available_categories):
            if cat == current_cat:
                continue
            if cat == "__unified__":
                options.append(discord.SelectOption(label="Unified Channel", value=cat))
            elif cat == config.DEFAULT_CATEGORY:
                options.append(discord.SelectOption(label="Ignore", value=cat))
            else:
                options.append(discord.SelectOption(label=cat.title(), value=cat))

        select = discord.ui.Select(
            placeholder=f"Currently: {current_cat} — pick a new category",
            options=options,
            min_values=1,
            max_values=1,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, select_interaction: discord.Interaction):
        """Handle category selection"""
        try:
            new_category = select_interaction.data["values"][0]
            logger.debug(f"RecategorizeView selection: '{new_category}' for entry {self.entry_id}")

            # Resolve the special "__unified__" token to the entry's original category
            if new_category == "__unified__":
                new_category = self.entry_data.get('original_category')
                if not new_category or new_category == config.DEFAULT_CATEGORY:
                    # Fallback: use the first non-ignore category
                    new_category = next((cat for cat in config.DISCORD_CHANNELS.keys() if cat != config.DEFAULT_CATEGORY), 'politics')
                logger.debug(f"Resolved '__unified__' token to category: {new_category}")

            # Defer immediately — recategorization can take a moment
            await select_interaction.response.defer(ephemeral=True)

            has_thread = self.message.thread is not None
            source_type = self.entry_id.split('_')[0] if self.entry_id else None

            logger.info(
                f"Re-categorizing entry {self.entry_id} from {self.current_category} to {new_category} (source_type: {source_type})"
            )

            success, new_message_id, new_channel_id, error_msg = await self.poster.recategorize_entry(
                message_id=self.message.id,
                channel_id=self.message.channel.id,
                new_category=new_category,
                entry_id=self.entry_id,
                content=self.entry_data.get('content', self.message.content),
                media_files=None,
                video_urls=self.entry_data.get('video_urls', []),
                source_type=source_type,
                user=select_interaction.user
            )

            if success:
                success_msg = f"✅ Successfully re-categorized from **{self.current_category}** to **{new_category}**!\n"
                success_msg += f"New message ID: {new_message_id}"
                if has_thread:
                    success_msg += "\n🧵 Thread with Perplexity content preserved!"
                await select_interaction.followup.send(success_msg, ephemeral=True)
                logger.info(f"Successfully re-categorized entry {self.entry_id} to {new_category}")
            else:
                await select_interaction.followup.send(
                    f"❌ Failed to re-categorize: {error_msg}",
                    ephemeral=True
                )
                logger.error(f"Failed to re-categorize entry {self.entry_id}: {error_msg}")

            # Disable the select after use so it can't be clicked again
            self.children[0].disabled = True
            await select_interaction.edit_original_response(view=self)

        except Exception as e:
            logger.error(f"Error in RecategorizeView _on_select: {e}", exc_info=True)
            try:
                if select_interaction.response.is_done():
                    await select_interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)
                else:
                    await select_interaction.response.send_message(f"❌ An error occurred: {str(e)}", ephemeral=True)
            except Exception as followup_error:
                logger.error(f"Failed to send error followup: {followup_error}")

    async def on_timeout(self):
        """Disable the select when the view times out"""
        self.children[0].disabled = True


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

            # Reconstruct the full message text (same logic as post_message),
            # re-appending hidden video URL links so they aren't lost on edit.
            video_urls = self.entry_data.get('video_urls', [])
            source_type = self.entry_data.get('source_type') or (self.entry_id.split('_')[0] if self.entry_id else None)

            message_text = ensure_url_on_own_line(new_text)

            # Re-prepend category tag in unified channel mode
            category = self.entry_data.get('category')
            if config.UNIFIED_CHANNEL_MODE and category and category != config.DEFAULT_CATEGORY:
                message_text = f"**[{category.title()}]**\n{message_text}"

            suppress_embeds = True

            if source_type == 'twitter' and video_urls:
                suppress_embeds = False
                for video_url in video_urls:
                    if video_url.startswith('http'):
                        message_text += f" [.]({video_url})"

            # Edit the Discord message — match the format it was originally posted in
            try:
                if self.message.embeds and not self.message.content:
                    # Originally posted as a Discord embed (long Telegram) — update the embed description
                    if len(message_text) > 4093:
                        message_text = message_text[:4093] + "..."
                    updated_embed = discord.Embed(description=message_text, color=discord.Color.dark_grey())
                    await self.message.edit(embed=updated_embed)
                else:
                    await self.message.edit(content=message_text, suppress=suppress_embeds)
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


class SetCategoryView(discord.ui.View):
    """Ephemeral view with a Select dropdown for re-categorizing entry category tags"""

    def __init__(self, current_cat, available_categories, entry_id, entry_data, message, poster):
        super().__init__(timeout=60)
        self.current_category = current_cat
        self.entry_id = entry_id
        self.entry_data = entry_data
        self.message = message
        self.poster = poster

        # Build select options — all categories except the current one
        options = [
            discord.SelectOption(label=cat.title(), value=cat)
            for cat in sorted(available_categories)
            if cat != current_cat
        ]

        select = discord.ui.Select(
            placeholder=f"Currently: {current_cat} — pick a new category",
            options=options,
            min_values=1,
            max_values=1,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, select_interaction: discord.Interaction):
        """Handle category selection for label-only update"""
        try:
            new_category = select_interaction.data["values"][0]
            logger.debug(f"SetCategoryView selection: '{new_category}' for entry {self.entry_id}")

            # Defer immediately — update can take a moment
            await select_interaction.response.defer(ephemeral=True)

            logger.info(
                f"Updating category tag for entry {self.entry_id} from {self.current_category} to {new_category}"
            )

            success, error_msg = await self.poster.update_category_tag(
                message_id=self.message.id,
                channel_id=self.message.channel.id,
                new_category=new_category,
                entry_id=self.entry_id,
                content=self.entry_data.get('content', self.message.content),
                user=select_interaction.user
            )

            if success:
                success_msg = f"✅ Successfully updated category from **{self.current_category}** to **{new_category}**!"
                await select_interaction.followup.send(success_msg, ephemeral=True)
                logger.info(f"Successfully updated category tag for entry {self.entry_id} to {new_category}")
            else:
                await select_interaction.followup.send(
                    f"❌ Failed to update category: {error_msg}",
                    ephemeral=True
                )
                logger.error(f"Failed to update category for entry {self.entry_id}: {error_msg}")

            # Disable the select after use so it can't be clicked again
            self.children[0].disabled = True
            await select_interaction.edit_original_response(view=self)

        except Exception as e:
            logger.error(f"Error in SetCategoryView _on_select: {e}", exc_info=True)
            try:
                if select_interaction.response.is_done():
                    await select_interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)
                else:
                    await select_interaction.response.send_message(f"❌ An error occurred: {str(e)}", ephemeral=True)
            except Exception as followup_error:
                logger.error(f"Failed to send error followup: {followup_error}")

    async def on_timeout(self):
        """Disable the select when the view times out"""
        self.children[0].disabled = True

