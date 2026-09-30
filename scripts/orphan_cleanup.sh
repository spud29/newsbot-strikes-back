#!/bin/bash
# NewsBot orphan cleanup — runs weekly via cron
# Finds Discord messages posted by the bot with no DB entry and deletes them.

export PYTHONPATH="/home/brandi/newsbot strikes back:$PYTHONPATH"
export DISCORD_TOKEN=$(grep "^DISCORD_TOKEN=" /home/brandi/newsbot\ strikes\ back/.env 2>/dev/null | cut -d= -f2-)
export TELEGRAM_API_ID=$(grep "^TELEGRAM_API_ID=" /home/brandi/newsbot\ strikes\ back/.env 2>/dev/null | cut -d= -f2-)
export TELEGRAM_API_HASH=$(grep "^TELEGRAM_API_HASH=" /home/brandi/newsbot\ strikes\ back/.env 2>/dev/null | cut -d= -f2-)
export PERPLEXITY_API_KEY=$(grep "^PERPLEXITY_API_KEY=" /home/brandi/newsbot\ strikes\ back/.env 2>/dev/null | cut -d= -f2-)
export OPENROUTER_API_KEY=$(grep "^OPENROUTER_API_KEY=" /home/brandi/newsbot\ strikes\ back/.env 2>/dev/null | cut -d= -f2-)
export LOCALAPPDATA=/home/brandi/.local/share

cd "/home/brandi/newsbot strikes back"
.venv/bin/python orphan_cleanup.py 2>&1
