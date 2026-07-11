"""
Discord UI components (modals, views) for the news aggregator bot
"""
import discord
import asyncio
import config
import re
from utils import logger, ensure_url_on_own_line, shorten_urls_in_text


_UNCHANGED = object()


class ReasonModal(discord.ui.Modal, title="Why this change? (optional)"):
    """Modal that captures an optional user reason before executing a re-categorization."""

    reason = discord.ui.TextInput(
        label="Reason (helps AI learn your preferences)",
        placeholder="e.g. 'SEC enforcement is politics, not a crypto market event'",
        required=False,
        max_length=300,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, new_category, current_category, entry_id, entry_data, message, poster, source_type, is_move, new_secondary=_UNCHANGED):
        super().__init__()
        self.new_category = new_category
        self.current_category = current_category
        self.entry_id = entry_id
        self.entry_data = entry_data
        self.message = message
        self.poster = poster
        self.source_type = source_type
        self.is_move = is_move
        self.new_secondary = new_secondary

    async def on_submit(self, interaction: discord.Interaction):
        user_reason = self.reason.value.strip() or None
        await interaction.response.defer(ephemeral=True)

        # Apply pending secondary category change before primary (so tag rebuild picks it up)
        if self.new_secondary is not _UNCHANGED and self.poster.database:
            self.poster.database.update_message_mapping_fields(
                self.entry_id, secondary_category=self.new_secondary
            )

        if self.is_move:
            has_thread = self.message.thread is not None
            success, new_message_id, new_channel_id, error_msg = await self.poster.recategorize_entry(
                message_id=self.message.id,
                channel_id=self.message.channel.id,
                new_category=self.new_category,
                entry_id=self.entry_id,
                content=self.entry_data.get('content', self.message.content),
                media_files=None,
                video_urls=self.entry_data.get('video_urls', []),
                source_type=self.source_type,
                user=interaction.user,
                user_reason=user_reason,
            )
            if not success:
                await interaction.followup.send(f"❌ Failed to re-categorize: {error_msg}", ephemeral=True)
        else:
            success, error_msg = await self.poster.update_category_tag(
                message_id=self.message.id,
                channel_id=self.message.channel.id,
                new_category=self.new_category,
                entry_id=self.entry_id,
                content=self.entry_data.get('content', self.message.content),
                user=interaction.user,
                user_reason=user_reason,
            )
            if not success:
                await interaction.followup.send(f"❌ Failed to update category: {error_msg}", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"ReasonModal error: {error}", exc_info=True)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ An error occurred: {error}", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ An error occurred: {error}", ephemeral=True)
        except Exception:
            pass


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
        """Handle category selection — show reason modal before acting."""
        if getattr(self, '_submitted', False):
            await select_interaction.response.send_message("Already submitted.", ephemeral=True)
            return
        self._submitted = True

        try:
            new_category = select_interaction.data["values"][0]
            logger.debug(f"RecategorizeView selection: '{new_category}' for entry {self.entry_id}")

            # Resolve the special "__unified__" token to the entry's original category
            if new_category == "__unified__":
                new_category = self.entry_data.get('original_category')
                if not new_category or new_category == config.DEFAULT_CATEGORY:
                    new_category = next((cat for cat in config.DISCORD_CHANNELS.keys() if cat != config.DEFAULT_CATEGORY), config.FALLBACK_CATEGORY)
                logger.debug(f"Resolved '__unified__' token to category: {new_category}")

            source_type = self.entry_id.split('_')[0] if self.entry_id else None
            logger.info(
                f"Re-categorizing entry {self.entry_id} from {self.current_category} to {new_category} (source_type: {source_type})"
            )

            if config.REASON_MODAL_ENABLED:
                await select_interaction.response.send_modal(
                    ReasonModal(
                        new_category=new_category,
                        current_category=self.current_category,
                        entry_id=self.entry_id,
                        entry_data=self.entry_data,
                        message=self.message,
                        poster=self.poster,
                        source_type=source_type,
                        is_move=True,
                    )
                )
            else:
                await select_interaction.response.defer(ephemeral=True)
                has_thread = self.message.thread is not None
                success, new_message_id, new_channel_id, error_msg = await self.poster.recategorize_entry(
                    message_id=self.message.id,
                    channel_id=self.message.channel.id,
                    new_category=new_category,
                    entry_id=self.entry_id,
                    content=self.entry_data.get('content', self.message.content),
                    media_files=None,
                    video_urls=self.entry_data.get('video_urls', []),
                    source_type=source_type,
                    user=select_interaction.user,
                    user_reason=None,
                )
                if not success:
                    await select_interaction.followup.send(f"❌ Failed to re-categorize: {error_msg}", ephemeral=True)

        except Exception as e:
            logger.error(f"Error in RecategorizeView _on_select: {e}", exc_info=True)
            self._submitted = False
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

            # Shorten URLs to TinyURLs (same guard as post_message)
            _url_count = len(re.findall(r'https?://\S+', message_text))
            _is_dexerto = 'dexerto.com' in message_text
            if _url_count <= 1 or _is_dexerto:
                message_text = await asyncio.to_thread(shorten_urls_in_text, message_text)

            # Re-prepend category tag in unified channel mode
            category = self.entry_data.get('category')
            if config.UNIFIED_CHANNEL_MODE and category and category != config.DEFAULT_CATEGORY:
                from discord_messaging import _format_category_tag
                secondary_cat = self.entry_data.get('secondary_category')
                tag = _format_category_tag(category, secondary_cat)
                message_text = f"{tag}\n{message_text}"

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

            confirmation = await modal_interaction.followup.send(
                "✅ Entry text updated successfully.",
                ephemeral=True
            )
            try:
                await confirmation.delete()
            except Exception:
                pass

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
    """Ephemeral view with Select dropdowns for primary and secondary category tags.

    Both selects store pending values; the Confirm button applies them together.
    """

    def __init__(self, current_cat, available_categories, entry_id, entry_data, message, poster):
        super().__init__(timeout=60)
        self.current_category = current_cat
        self.current_secondary = entry_data.get('secondary_category') if entry_data else None
        self.entry_id = entry_id
        self.entry_data = entry_data
        self.message = message
        self.poster = poster
        self._pending_primary = None
        self._pending_secondary = _UNCHANGED
        self._confirmed = False

        # Primary category dropdown (row 0)
        primary_options = [
            discord.SelectOption(label=cat.title(), value=cat)
            for cat in sorted(available_categories)
        ]
        primary_select = discord.ui.Select(
            placeholder=f"Primary: {current_cat} — pick a new category",
            options=primary_options,
            min_values=1,
            max_values=1,
            row=0,
        )
        primary_select.callback = self._on_primary_select
        self.add_item(primary_select)

        # Secondary category dropdown (row 1)
        secondary_options = [discord.SelectOption(label="None (clear)", value="__none__")]
        for cat in sorted(available_categories):
            if cat != config.DEFAULT_CATEGORY and cat != current_cat:
                is_current = (cat == self.current_secondary)
                secondary_options.append(discord.SelectOption(
                    label=cat.title(), value=cat, default=is_current
                ))
        sec_label = self.current_secondary or "none"
        secondary_select = discord.ui.Select(
            placeholder=f"Secondary: {sec_label} — pick or clear",
            options=secondary_options,
            min_values=1,
            max_values=1,
            row=1,
        )
        secondary_select.callback = self._on_secondary_select
        self.add_item(secondary_select)

        # Confirm button (row 2)
        confirm_btn = discord.ui.Button(
            label="Confirm", style=discord.ButtonStyle.primary, row=2
        )
        confirm_btn.callback = self._on_confirm
        self.add_item(confirm_btn)

    async def _on_primary_select(self, interaction: discord.Interaction):
        self._pending_primary = interaction.data["values"][0]
        await interaction.response.defer()

    async def _on_secondary_select(self, interaction: discord.Interaction):
        selected = interaction.data["values"][0]
        self._pending_secondary = None if selected == "__none__" else selected
        await interaction.response.defer()

    async def _on_confirm(self, interaction: discord.Interaction):
        if self._confirmed:
            await interaction.response.send_message("Already confirmed.", ephemeral=True)
            return

        primary_changed = (
            self._pending_primary is not None
            and self._pending_primary != self.current_category
        )
        secondary_changed = (
            self._pending_secondary is not _UNCHANGED
            and self._pending_secondary != self.current_secondary
        )

        if not primary_changed and not secondary_changed:
            await interaction.response.send_message(
                "No changes selected — pick from the dropdowns above first.",
                ephemeral=True,
            )
            return

        self._confirmed = True

        try:
            if primary_changed:
                new_cat = self._pending_primary
                logger.info(
                    f"Updating category tag for {self.entry_id}: "
                    f"{self.current_category} -> {new_cat}"
                    + (f", secondary -> {self._pending_secondary}" if secondary_changed else "")
                )
                if config.REASON_MODAL_ENABLED:
                    await interaction.response.send_modal(
                        ReasonModal(
                            new_category=new_cat,
                            current_category=self.current_category,
                            entry_id=self.entry_id,
                            entry_data=self.entry_data,
                            message=self.message,
                            poster=self.poster,
                            source_type=None,
                            is_move=False,
                            new_secondary=self._pending_secondary if secondary_changed else _UNCHANGED,
                        )
                    )
                else:
                    await interaction.response.defer(ephemeral=True)
                    if secondary_changed and self.poster.database:
                        self.poster.database.update_message_mapping_fields(
                            self.entry_id, secondary_category=self._pending_secondary
                        )
                    success, error_msg = await self.poster.update_category_tag(
                        message_id=self.message.id,
                        channel_id=self.message.channel.id,
                        new_category=new_cat,
                        entry_id=self.entry_id,
                        content=self.entry_data.get('content', self.message.content),
                        user=interaction.user,
                        user_reason=None,
                    )
                    if success:
                        await interaction.delete_original_response()
                    else:
                        await interaction.followup.send(f"❌ Failed to update category: {error_msg}", ephemeral=True)
            else:
                # Only secondary changed — apply directly, no reason modal needed
                await interaction.response.defer(ephemeral=True)
                new_secondary = self._pending_secondary

                if self.poster.database:
                    self.poster.database.update_message_mapping_fields(
                        self.entry_id, secondary_category=new_secondary
                    )

                from discord_messaging import _format_category_tag
                category = self.entry_data.get('category') or self.current_category
                content = self.entry_data.get('content', self.message.content) or ''

                _base = ensure_url_on_own_line(content)
                _url_count = len(re.findall(r'https?://\S+', _base))
                _is_dexerto = 'dexerto.com' in _base
                if _url_count <= 1 or _is_dexerto:
                    _base = await asyncio.to_thread(shorten_urls_in_text, _base)

                new_text = f"{_format_category_tag(category, new_secondary)}\n{_base}"

                video_urls = self.entry_data.get('video_urls', [])
                source_type = self.entry_data.get('source_type')
                suppress_embeds = True
                if source_type == 'twitter' and video_urls:
                    suppress_embeds = False
                    for video_url in video_urls:
                        if video_url.startswith('http'):
                            new_text += f" [.]({video_url})"

                if self.message.embeds and not self.message.content:
                    if len(new_text) > 4093:
                        new_text = new_text[:4093] + "..."
                    updated_embed = discord.Embed(description=new_text, color=discord.Color.dark_grey())
                    await self.message.edit(embed=updated_embed)
                else:
                    if len(new_text) > 2000:
                        new_text = new_text[:1997] + "..."
                    await self.message.edit(content=new_text, suppress=suppress_embeds)

                logger.info(f"Set secondary category for {self.entry_id} to {new_secondary}")
                await interaction.delete_original_response()

        except Exception as e:
            logger.error(f"Error in SetCategoryView _on_confirm: {e}", exc_info=True)
            self._confirmed = False
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(f"An error occurred: {str(e)}", ephemeral=True)
                else:
                    await interaction.response.send_message(f"An error occurred: {str(e)}", ephemeral=True)
            except Exception as followup_error:
                logger.error(f"Failed to send error followup: {followup_error}")

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True



