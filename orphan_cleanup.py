#!/usr/bin/env python3
"""
Orphan cleanup script for NewsBot.

Runs weekly to find Discord messages posted by the bot that have no
corresponding database entry. These are "orphans" — typically left behind
when the deferred delete in recategorize_entry silently failed.

Deletes orphan messages and logs what it found.
"""

import asyncio
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault('LOCALAPPDATA', '/home/brandi/.local/share')

import config
import discord
from database import Database
from discord_poster import DiscordPoster
from utils import logger


async def find_and_clean_orphans():
    """Find bot messages without DB entries and delete them."""
    db = Database()
    poster = DiscordPoster(db)
    await poster.start()

    orphan_count = 0
    deleted_count = 0
    skipped_count = 0

    # Check every channel the bot posts to
    channels_to_check = list(config.DISCORD_CHANNELS.values())

    for channel_id in channels_to_check:
        channel = poster.client.get_channel(channel_id)
        if not channel:
            try:
                channel = await poster.client.fetch_channel(channel_id)
            except Exception as e:
                logger.warning(f"Cannot fetch channel {channel_id}: {e}")
                continue

        logger.info(f"Scanning channel {channel_id} ({channel.name if channel else 'unknown'})...")

        # Iterate recent messages (last 100) from the channel
        try:
            async for msg in channel.history(limit=100):
                if msg.author != poster.client.user:
                    continue

                # Check if this message has a DB entry
                entry_id = db.get_entry_id_by_discord_message(msg.id)
                if entry_id:
                    continue

                # No DB entry — this is an orphan
                orphan_count += 1
                content_preview = msg.content[:80].replace('\n', ' ') if msg.content else '(empty)'
                logger.info(
                    f"Orphan found: msg {msg.id} in channel {channel_id} "
                    f"by {msg.author}: \"{content_preview}...\""
                )

                # Try to delete it
                try:
                    await msg.delete()
                    deleted_count += 1
                    logger.info(f"  Deleted orphan message {msg.id}")
                except discord.Forbidden:
                    logger.warning(f"  Cannot delete orphan {msg.id}: bot lacks permission")
                    skipped_count += 1
                except discord.NotFound:
                    logger.info(f"  Orphan {msg.id} already deleted")
                    skipped_count += 1
                except Exception as e:
                    logger.error(f"  Failed to delete orphan {msg.id}: {e}")
                    skipped_count += 1
        except Exception as e:
            logger.error(f"Error fetching history from channel {channel_id}: {e}")
            continue

    await poster.stop()

    logger.info(
        f"Orphan cleanup complete: {orphan_count} orphans found, "
        f"{deleted_count} deleted, {skipped_count} skipped"
    )

    if orphan_count > 0:
        print(f"Orphan cleanup: found {orphan_count}, deleted {deleted_count}, skipped {skipped_count}")
    else:
        print("Orphan cleanup: no orphans found")


if __name__ == '__main__':
    asyncio.run(find_and_clean_orphans())
