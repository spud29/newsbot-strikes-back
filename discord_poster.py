"""
Discord poster - re-exports DiscordPoster for backward compatibility.

The implementation has been split into:
  - discord_messaging.py: DiscordPoster class (posting, editing, recategorization)
  - discord_commands.py: Context menu command registration
  - discord_ui.py: UI components (RecategorizeModal)
"""
from discord_messaging import DiscordPoster

__all__ = ['DiscordPoster']
