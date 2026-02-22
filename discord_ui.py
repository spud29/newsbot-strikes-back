"""
Discord UI components (modals, views) for the news aggregator bot
"""
import discord
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
