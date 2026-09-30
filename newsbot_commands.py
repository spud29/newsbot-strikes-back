#!/usr/bin/env python3
"""
NewsBot CLI — inspect, move, repair, and manage entries from the terminal.

Usage:
    newsbot show <entry_id>              Show DB state + verify Discord message
    newsbot move <entry_id> <category>  Move entry to a different category channel
    newsbot repair <entry_id> <category> Re-post entry whose DB says posted but message missing
    newsbot images <entry_id> <category> Re-download images from source, then post to category
    newsbot repost <entry_id> <category> Alias for images — re-download media + post
    newsbot list-ignore                  List entries in ignore channel
    newsbot scan                         Scan ignore channel for unreviewed entries
    newsbot fix <entry_id>              Clear dangling message IDs from DB
    newsbot state                        Show overall bot state

All post operations use the Discord REST API directly (no bot instance needed),
so they work even when the NewsBot service is running.
"""
import sys
import os
import argparse
import sqlite3
import asyncio
import aiohttp
import subprocess
import tempfile
import re
import threading
import io
import json
from datetime import datetime, timezone

# ── Setup ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

if 'PYTHONPATH' in os.environ:
    if PROJECT_ROOT not in os.environ['PYTHONPATH']:
        del os.environ['PYTHONPATH']
os.environ['PYTHONPATH'] = PROJECT_ROOT

import config

from discord_messaging import (
    _format_category_tag,
    ensure_url_on_own_line,
    shorten_urls_in_text,
)


# ── Database ───────────────────────────────────────────────────────────────────

def get_db():
    db_path = os.path.join(PROJECT_ROOT, 'data', 'newsbot.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def print_entry(row, show_discord_status=False, discord_msg_exists=False, discord_msg_missing_reason=None):
    print(f"\n  Entry ID:        {row['entry_id']}")
    print(f"  Category:        {row['category']}")
    print(f"  Channel ID:      {row['discord_channel_id']}")
    print(f"  Message ID:      {row['discord_message_id']}")
    print(f"  Source:          {row['source_type']} | {row['source_url']}")
    print(f"  Newsworthiness:  {row['newsworthiness_score']}/10")
    print(f"  User edited:     {row['user_edited']}")
    print(f"  Original cat:    {row['original_category']}")
    print(f"  Secondary:       {row['secondary_category']}")
    print(f"  Placement:       {row['placement_reason'] or 'None'}")
    
    content = row['content'] or ''
    if len(content) > 300:
        print(f"\n  Content ({len(content)} chars):\n  {content[:300]}...")
    else:
        print(f"\n  Content ({len(content)} chars):\n  {content}")
    
    if show_discord_status:
        if discord_msg_exists:
            print(f"\n  ✓ Discord message EXISTS in recorded channel")
        else:
            print(f"\n  ✗ Discord message MISSING from recorded channel")
            if discord_msg_missing_reason:
                print(f"    Reason: {discord_msg_missing_reason}")


# ── Discord REST API helpers ─────────────────────────────────────────────────

API_BASE = "https://discord.com/api/v10"
HEADERS = {"Authorization": f"Bot {config.DISCORD_TOKEN}"}


async def api_get(session, path):
    async with session.get(f"{API_BASE}{path}", headers=HEADERS) as r:
        if r.status == 200:
            return await r.json()
        return None


async def api_post(session, path, **kwargs):
    async with session.post(f"{API_BASE}{path}", headers=HEADERS, **kwargs) as r:
        if r.status == 200:
            return await r.json()
        text = await r.text()
        return None, r.status, text


async def check_message_exists(channel_id, message_id):
    """Check if a Discord message exists via REST API."""
    async with aiohttp.ClientSession() as s:
        msg = await api_get(s, f"/channels/{channel_id}/messages/{message_id}")
        return msg is not None, "Not found" if msg is None else None


async def get_channel_info(channel_id):
    """Get channel name via REST API."""
    async with aiohttp.ClientSession() as s:
        info = await api_get(s, f"/channels/{channel_id}")
        if info:
            return info.get('name', f'<#{channel_id}>')
    return f'<#{channel_id}>'


async def post_to_channel(channel_id, content, files_data=None, nonce=None):
    """Post a message (with optional files) to a Discord channel via REST API.
    
    files_data: list of (filename, bytes) tuples
    Returns (message_id, error_msg).
    """
    async with aiohttp.ClientSession() as s:
        form = aiohttp.FormData()
        form.add_field('content', content)
        if nonce:
            form.add_field('nonce', nonce)
        
        if files_data:
            for i, (fname, data) in enumerate(files_data):
                form.add_field(f'files[{i}]', data, filename=fname, content_type='image/jpeg')
        
        result = await api_post(s, f"/channels/{channel_id}/messages", data=form)
        if isinstance(result, tuple):
            return None, f"HTTP {result[1]}: {result[2][:200]}"
        return result['id'], None


# ── Image download from Twitter/X ─────────────────────────────────────────────

async def download_images_from_source(entry_id, source_url, output_dir):
    """Use gallery-dl to extract images from a Twitter/X source URL.
    
    Returns list of (filename, bytes) tuples for downloaded images.
    """
    if not source_url:
        print("  No source URL — cannot download images")
        return []
    
    print(f"  Downloading images from: {source_url}")
    
    try:
        result = await asyncio.to_thread(
            lambda: subprocess.run(
                [
                    sys.executable, '-m', 'gallery_dl',
                    '--config', os.path.expanduser('~/.config/gallery-dl/config.json'),
                    '--dump-json',
                    '--no-download',
                    source_url,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        )
        
        if result.returncode != 0:
            print(f"  gallery-dl metadata fetch failed (rc={result.returncode})")
            if result.stderr:
                print(f"    stderr: {result.stderr[:200]}")
            return []
        
        try:
            data = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            print(f"  gallery-dl output not valid JSON")
            return []
        
        # Collect image URLs (type 3 items with photo type)
        image_urls = []
        for item in data:
            if not isinstance(item, list) or len(item) < 3:
                continue
            if item[0] == 3 and isinstance(item[1], str):
                meta = item[2]
                if isinstance(meta, dict) and meta.get('type') == 'photo':
                    image_urls.append(item[1])
        
        if not image_urls:
            print(f"  No image URLs found in tweet metadata")
            return []
        
        print(f"  Found {len(image_urls)} image(s) in tweet")
        
        # Download each image
        downloaded = []
        for i, url in enumerate(image_urls):
            try:
                basename = url.split('/')[-1].split('?')[0]
                ext = os.path.splitext(basename)[1] or '.jpg'
                filename = f"{entry_id}_{i+1}{ext}"
                fpath = os.path.join(output_dir, filename)
                
                async with aiohttp.ClientSession() as s:
                    async with s.get(url) as r:
                        if r.status == 200:
                            data = await r.read()
                            with open(fpath, 'wb') as f:
                                f.write(data)
                            downloaded.append((filename, data))
                            print(f"  Downloaded: {filename} ({len(data)} bytes)")
                        else:
                            print(f"  Failed to download image {i+1}: HTTP {r.status}")
            except Exception as e:
                print(f"  Error downloading image {i+1}: {e}")
        
        return downloaded
        
    except asyncio.TimeoutError:
        print("  gallery-dl timed out")
        return []
    except Exception as e:
        print(f"  Error: {e}")
        return []


# ── Content formatting ─────────────────────────────────────────────────────────

def format_content(content, original_category, secondary_category):
    """Apply category tag and cleanup to content."""
    _base = ensure_url_on_own_line(content)
    _url_count = len(re.findall(r'https?://\S+', _base))
    _is_dexerto = 'dexerto.com' in _base
    _is_polymarket = 'poly.market' in _base or 'polymarket.com' in _base
    if _url_count <= 1 or _is_dexerto or _is_polymarket:
        _base = shorten_urls_in_text(_base)
    
    new_text = f"{_format_category_tag(original_category, secondary_category)}\n{_base}"
    if len(new_text) > 2000:
        new_text = new_text[:1997] + "..."
    return new_text


# ── Commands ───────────────────────────────────────────────────────────────────

async def cmd_show(entry_id, verbose=False):
    """Show DB state for an entry and optionally verify Discord message."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM message_mapping WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        
        if not row:
            print(f"No entry found with ID: {entry_id}")
            return 1
        
        print_entry(row)
        
        if verbose and row['discord_message_id'] and row['discord_channel_id']:
            exists, reason = await check_message_exists(row['discord_channel_id'], row['discord_message_id'])
            print_entry(row, show_discord_status=True,
                      discord_msg_exists=exists,
                      discord_msg_missing_reason=reason)
        
        return 0
    finally:
        conn.close()


async def cmd_move(entry_id, new_category):
    """Move entry via Discord REST API — delete from old channel, post to new."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM message_mapping WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        
        if not row:
            print(f"No entry found: {entry_id}")
            return 1
        
        old_msg_id = row['discord_message_id']
        old_ch_id = row['discord_channel_id']
        
        if not old_msg_id:
            print("Entry has no Discord message ID — use 'repair' instead")
            return 1
        
        # Find target channel
        new_ch_id = None
        for cat, ch_id in config.DISCORD_CHANNELS.items():
            if cat.lower() == new_category.lower():
                new_ch_id = ch_id
                break
        
        if not new_ch_id:
            cats = list(config.DISCORD_CHANNELS.keys())
            print(f"Unknown category: {new_category}")
            print(f"Available: {', '.join(cats)}")
            return 1
        
        print(f"Moving {entry_id} → {new_category}")
        print(f"  From: {old_ch_id}/{old_msg_id}")
        
        # Check old message exists
        exists, reason = await check_message_exists(old_ch_id, old_msg_id)
        if not exists:
            print(f"  Original message not found ({reason}) — use repair instead")
            return 1
        
        # Delete old message
        print(f"  Deleting original message...")
        async with aiohttp.ClientSession() as s:
            async with s.delete(f"{API_BASE}/channels/{old_ch_id}/messages/{old_msg_id}", headers=HEADERS) as r:
                if r.status in (200, 204, 404):
                    print(f"  Deleted (status={r.status})")
                else:
                    print(f"  Delete returned {r.status} — continuing with repost")
        
        # Format content
        new_text = format_content(row['content'], row['original_category'] or new_category, row['secondary_category'])
        
        # Post to new channel
        print(f"  Posting to {new_category} (channel {new_ch_id})...")
        msg_id, error = await post_to_channel(new_ch_id, new_text, nonce=entry_id[:25])
        
        if msg_id:
            print(f"✓ Moved to {new_category}")
            print(f"  New message: {msg_id}")
            
            conn.execute("""
                UPDATE message_mapping 
                SET discord_message_id = ?, discord_channel_id = ?, user_edited = 1 
                WHERE entry_id = ?
            """, (msg_id, new_ch_id, entry_id))
            conn.commit()
            return 0
        else:
            print(f"✗ Post failed: {error}")
            return 1
    finally:
        conn.close()


async def cmd_repair(entry_id, target_category):
    """Re-post entry whose DB says posted but message doesn't exist.
    
    Re-downloads media from original Discord message if still available,
    then posts text + media to target channel.
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM message_mapping WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        
        if not row:
            print(f"No entry found: {entry_id}")
            return 1
        
        old_ch_id = row['discord_channel_id']
        old_msg_id = row['discord_message_id']
        content = row['content'] or ''
        orig_cat = row['original_category'] or target_category
        sec_cat = row['secondary_category']
        
        # Find target channel
        target_ch_id = None
        for cat, ch_id in config.DISCORD_CHANNELS.items():
            if cat.lower() == target_category.lower():
                target_ch_id = ch_id
                break
        
        if not target_ch_id:
            cats = list(config.DISCORD_CHANNELS.keys())
            print(f"Unknown category: {target_category}")
            print(f"Available: {', '.join(cats)}")
            return 1
        
        # Check if old message still exists for media re-download
        media_files = []
        if old_msg_id and old_ch_id:
            print(f"Checking original message for attachments...")
            exists, reason = await check_message_exists(old_ch_id, old_msg_id)
            if exists:
                print(f"  Original message exists — downloading attachments...")
                async with aiohttp.ClientSession() as s:
                    msg = await api_get(s, f"/channels/{old_ch_id}/messages/{old_msg_id}")
                    if msg and msg.get('attachments'):
                        for att in msg['attachments']:
                            fname = att['filename']
                            async with s.get(att['url']) as r:
                                if r.status == 200:
                                    data = await r.read()
                                    media_files.append((fname, data))
                                    print(f"  Downloaded: {fname} ({len(data)} bytes)")
            else:
                print(f"  Original message not found — posting text only")
        
        # Format content
        new_text = format_content(content, orig_cat, sec_cat)
        
        # Post
        print(f"\nPosting to {target_category}...")
        msg_id, error = await post_to_channel(target_ch_id, new_text, media_files, nonce=entry_id[:25])
        
        if msg_id:
            print(f"✓ Repaired: message {msg_id} in {target_category}")
            conn.execute("""
                UPDATE message_mapping 
                SET discord_message_id = ?, discord_channel_id = ?, user_edited = 1 
                WHERE entry_id = ?
            """, (msg_id, target_ch_id, entry_id))
            conn.commit()
            return 0
        else:
            print(f"✗ Repair failed: {error}")
            return 1
    finally:
        conn.close()


async def cmd_images(entry_id, target_category):
    """Re-download images from source URL, then post to channel."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM message_mapping WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        
        if not row:
            print(f"No entry found: {entry_id}")
            return 1
        
        content = row['content'] or ''
        source_url = row['source_url']
        orig_cat = row['original_category'] or target_category
        sec_cat = row['secondary_category']
        
        # Find target channel
        target_ch_id = None
        for cat, ch_id in config.DISCORD_CHANNELS.items():
            if cat.lower() == target_category.lower():
                target_ch_id = ch_id
                break
        
        if not target_ch_id:
            cats = list(config.DISCORD_CHANNELS.keys())
            print(f"Unknown category: {target_category}")
            print(f"Available: {', '.join(cats)}")
            return 1
        
        # Step 1: Download images from source
        print(f"\n=== Re-downloading images for {entry_id} ===")
        tmpdir = tempfile.mkdtemp(prefix=f"newsbot_imgs_{entry_id}_")
        img_data = await download_images_from_source(entry_id, source_url, tmpdir)
        
        if not img_data:
            print("\nNo images could be downloaded — posting text only")
        
        print(f"\nDownloaded {len(img_data)} image(s)")
        
        # Step 2: Format and post
        new_text = format_content(content, orig_cat, sec_cat)
        
        print(f"\nPosting to {target_category} with {len(img_data)} image(s)...")
        msg_id, error = await post_to_channel(target_ch_id, new_text, img_data, nonce=entry_id[:25])
        
        if msg_id:
            print(f"✓ Posted: message {msg_id}")
            conn.execute("""
                UPDATE message_mapping 
                SET discord_message_id = ?, discord_channel_id = ?, user_edited = 1 
                WHERE entry_id = ?
            """, (msg_id, target_ch_id, entry_id))
            conn.commit()
            
            # Verify
            exists, _ = await check_message_exists(target_ch_id, msg_id)
            if exists:
                async with aiohttp.ClientSession() as s:
                    msg = await api_get(s, f"/channels/{target_ch_id}/messages/{msg_id}")
                    if msg:
                        atts = msg.get('attachments', [])
                        print(f"  Verified: {len(atts)} attachment(s) on Discord")
                        for a in atts:
                            print(f"    - {a['filename']} ({a['size']} bytes)")
            return 0
        else:
            print(f"✗ Post failed: {error}")
            return 0
    finally:
        conn.close()
        # Cleanup
        import shutil
        try: shutil.rmtree(tmpdir)
        except: pass


async def cmd_repost(entry_id, target_category):
    """Alias for images — re-download media + post."""
    return await cmd_images(entry_id, target_category)


async def cmd_delete(entry_id):
    """Delete an entry from Discord and remove it from the database."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM message_mapping WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        
        if not row:
            print(f"No entry found: {entry_id}")
            return 1
        
        msg_id = row['discord_message_id']
        ch_id = row['discord_channel_id']
        category = row['category']
        
        if not msg_id or not ch_id:
            print(f"No message ID — clearing from DB only")
            conn.execute(
                "DELETE FROM message_mapping WHERE entry_id = ?", (entry_id,)
            )
            conn.commit()
            print(f"✓ Removed {entry_id} from database")
            return 0
        
        # Delete the Discord message
        print(f"Deleting message {msg_id} from channel {ch_id} ({category})...")
        async with aiohttp.ClientSession() as s:
            async with s.delete(f"{API_BASE}/channels/{ch_id}/messages/{msg_id}", headers=HEADERS) as r:
                if r.status == 204:
                    print(f"✓ Discord message deleted")
                else:
                    text = await r.text()
                    print(f"✗ Discord delete failed: HTTP {r.status}: {text[:200]}")
                    print(f"  Clearing DB entry anyway...")
        
        # Remove from database
        conn.execute(
            "DELETE FROM message_mapping WHERE entry_id = ?", (entry_id,)
        )
        conn.commit()
        print(f"✓ Removed {entry_id} from database")
        return 0
    finally:
        conn.close()


async def cmd_list_ignore():
    """List entries in the ignore channel."""
    conn = get_db()
    try:
        ignore_ch = config.DISCORD_CHANNELS.get('ignore')
        if not ignore_ch:
            print("No ignore channel configured")
            return 1
        
        rows = conn.execute(
            "SELECT * FROM message_mapping WHERE discord_channel_id = ? ORDER BY timestamp DESC LIMIT 50",
            (ignore_ch,)
        ).fetchall()
        
        if not rows:
            print("No entries in ignore channel")
            return 0
        
        print(f"Entries in ignore channel ({len(rows)} shown, latest first):\n")
        for row in rows:
            c = (row['content'] or '')[:120]
            print(f"  {row['entry_id']}")
            print(f"    {c}...")
            print(f"    Reason: {row['placement_reason']}")
            print(f"    Message ID: {row['discord_message_id']}")
            print()
        
        return 0
    finally:
        conn.close()


async def cmd_scan():
    """Scan ignore channel for unreviewed entries worth promoting."""
    conn = get_db()
    try:
        ignore_ch = config.DISCORD_CHANNELS.get('ignore')
        if not ignore_ch:
            print("No ignore channel configured")
            return 1
        
        rows = conn.execute(
            """SELECT * FROM message_mapping 
               WHERE discord_channel_id = ? 
               AND user_edited = 0
               AND placement_reason NOT LIKE '%%User re-categorization%%'
               ORDER BY timestamp DESC LIMIT 20""",
            (ignore_ch,)
        ).fetchall()
        
        if not rows:
            print("No unreviewed entries in ignore channel")
            return 0
        
        print(f"Unreviewed entries in ignore channel ({len(rows)} found):\n")
        for row in rows:
            c = (row['content'] or '')[:200]
            print(f"  [{row['entry_id']}]  score={row['newsworthiness_score']}/10")
            print(f"    {c}")
            print(f"    Reason: {row['placement_reason']}")
            print(f"    Orig cat: {row['original_category']} | Secondary: {row['secondary_category']}")
            print()
        
        return 0
    finally:
        conn.close()


async def cmd_fix(entry_id):
    """Detect and fix DB inconsistencies (dangling message IDs)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM message_mapping WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        
        if not row:
            print(f"No entry found: {entry_id}")
            return 1
        
        msg_id = row['discord_message_id']
        ch_id = row['discord_channel_id']
        
        if not msg_id or not ch_id:
            print("No message/channel ID to check")
            return 0
        
        print(f"Checking consistency for {entry_id}...")
        print(f"  DB says: channel={ch_id}, message={msg_id}")
        
        exists, reason = await check_message_exists(ch_id, msg_id)
        
        if exists:
            print(f"  ✓ Consistent: message exists in recorded channel")
            return 0
        else:
            print(f"  ✗ Inconsistent: {reason}")
            print(f"  Clearing dangling message ID...")
            conn.execute("""
                UPDATE message_mapping 
                SET discord_message_id = NULL, user_edited = 1 
                WHERE entry_id = ?
            """, (entry_id,))
            conn.commit()
            print(f"  ✓ Fixed. Use 'repost' to re-post.")
            return 0
    finally:
        conn.close()


async def cmd_state():
    """Show overall bot state."""
    conn = get_db()
    try:
        counts = conn.execute(
            """SELECT 
                (SELECT COUNT(*) FROM message_mapping) as mappings,
                (SELECT COUNT(*) FROM processed_ids) as processed,
                (SELECT COUNT(*) FROM embeddings) as embeddings,
                (SELECT COUNT(*) FROM removed_entries) as removed,
                (SELECT COUNT(*) FROM retry_queue) as retries
            """
        ).fetchone()
        
        print("=== NewsBot State ===\n")
        print(f"  Message mappings:  {counts['mappings']}")
        print(f"  Processed IDs:     {counts['processed']}")
        print(f"  Embeddings cache:  {counts['embeddings']}")
        print(f"  Removed entries:   {counts['removed']}")
        print(f"  Retry queue:       {counts['retries']}")
        
        recent = conn.execute(
            """SELECT category, source_type, content, timestamp 
               FROM message_mapping 
               ORDER BY timestamp DESC LIMIT 5"""
        ).fetchall()
        
        print(f"\n  Recent entries:")
        for row in recent:
            c = (row['content'] or '')[:80]
            ts = datetime.fromtimestamp(row['timestamp'], tz=timezone.utc)
            print(f"    [{ts.strftime('%H:%M UTC')}] [{row['category']}] ({row['source_type']}) {c}...")
        
        ignore_ch = config.DISCORD_CHANNELS.get('ignore')
        if ignore_ch:
            ic = conn.execute(
                "SELECT COUNT(*) FROM message_mapping WHERE discord_channel_id = ?",
                (ignore_ch,)
            ).fetchone()[0]
            print(f"\n  Ignore channel entries: {ic}")
        
        print()
        return 0
    finally:
        conn.close()


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(prog='newsbot', description='NewsBot CLI')
    sub = parser.add_subparsers(dest='command')
    
    p_show = sub.add_parser('show', help='Show DB state for an entry')
    p_show.add_argument('entry_id')
    p_show.add_argument('-v', '--verbose', action='store_true', help='Verify Discord message')
    
    p_move = sub.add_parser('move', help='Move entry to different category')
    p_move.add_argument('entry_id')
    p_move.add_argument('category')
    
    p_repair = sub.add_parser('repair', help='Re-post entry whose DB says posted but message missing')
    p_repair.add_argument('entry_id')
    p_repair.add_argument('category')
    
    p_images = sub.add_parser('images', help='Re-download images from source, then post')
    p_images.add_argument('entry_id')
    p_images.add_argument('category')
    
    p_repost = sub.add_parser('repost', help='Re-download media + post (same as images)')
    p_repost.add_argument('entry_id')
    p_repost.add_argument('category')
    
    sub.add_parser('list-ignore', help='List entries in ignore channel')
    sub.add_parser('scan', help='Scan ignore channel for unreviewed entries')
    
    p_fix = sub.add_parser('fix', help='Clear dangling message IDs from DB')
    p_fix.add_argument('entry_id')
    
    p_delete = sub.add_parser('delete', help='Delete entry from Discord + DB')
    p_delete.add_argument('entry_id')
    
    sub.add_parser('state', help='Show overall bot state')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    if args.command == 'show':
        return await cmd_show(args.entry_id, args.verbose)
    elif args.command == 'move':
        return await cmd_move(args.entry_id, args.category)
    elif args.command == 'repair':
        return await cmd_repair(args.entry_id, args.category)
    elif args.command == 'images':
        return await cmd_images(args.entry_id, args.category)
    elif args.command == 'repost':
        return await cmd_repost(args.entry_id, args.category)
    elif args.command == 'list-ignore':
        return await cmd_list_ignore()
    elif args.command == 'scan':
        return await cmd_scan()
    elif args.command == 'fix':
        return await cmd_fix(args.entry_id)
    elif args.command == 'delete':
        return await cmd_delete(args.entry_id)
    elif args.command == 'state':
        return await cmd_state()
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
