#!/usr/bin/env python3
"""
NewsBot Monitor CLI — evaluates entries in the ignore channel and promotes
the good ones to the main Discord channel.

Runs as a standalone tool using the NewsBot project's own DiscordPoster and
Database, so everything posts as NewsBot and updates the DB correctly.

Usage:
    .venv/bin/python newsbot_monitor.py scan                                   # scan for new ignore entries
    .venv/bin/python newsbot_monitor.py promote <entry_id> --category "us politics"  # promote one entry
    .venv/bin/python newsbot_monitor.py demote <entry_id>                      # move one entry back to ignore
    .venv/bin/python newsbot_monitor.py state                                  # show tracking state
    .venv/bin/python newsbot_monitor.py clear-state                            # reset tracking state
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

# Add project root to path so we can import newsbot modules
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

import config
import discord
from database import Database
from discord_poster import DiscordPoster
from removed_entries import RemovedEntriesDB
from feedback_profile import (
    load_feedback,
    rebuild_profile_from_db,
    update_profile_from_new_action,
    score_entry_against_profile,
    print_profile_summary,
)
from self_improvement import build_report

# State file lives alongside Hermes cache
STATE_DIR = os.path.expanduser("~/.hermes/cache")
STATE_FILE = os.path.join(STATE_DIR, "newsbot-monitor-state.json")

# The main channel category — entries promoted here go to the unified channel
# (or the default category channel if unified mode is off)
MAIN_CATEGORY = config.FALLBACK_CATEGORY  # "general news" — the catch-all

# Categories an entry can be promoted into (everything except 'ignore')
PROMOTE_CATEGORIES = [c for c in config.VALID_CATEGORIES if c != config.DEFAULT_CATEGORY]


def normalize_category(words):
    """
    Turn a --category value into a valid category name.

    Accepts a list of words so unquoted multi-word values work
    (`--category us politics` arrives as ['us', 'politics']). Tolerates
    stray quotes, case, underscores/hyphens, and 'and' for '&'.
    Raises ValueError with a "did you mean" hint on an unknown value.
    """
    import difflib

    raw = " ".join(words) if isinstance(words, (list, tuple)) else str(words)
    value = raw.strip().strip("'\"").lower().replace("_", " ").replace("-", " ")
    value = re.sub(r"\s+and\s+", " & ", value)
    value = re.sub(r"\s+", " ", value).strip()

    if value in PROMOTE_CATEGORIES:
        return value

    valid = ", ".join(f'"{c}"' for c in PROMOTE_CATEGORIES)
    suggestion = difflib.get_close_matches(value, PROMOTE_CATEGORIES, n=1, cutoff=0.5)
    hint = f' Did you mean "{suggestion[0]}"?' if suggestion else ""
    raise ValueError(f'invalid category "{raw}".{hint} Valid categories: {valid}')


def load_state():
    """Load the monitor's tracking state."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "last_scan_ts": 0,
        "evaluated_entries": [],  # entry_ids we've already evaluated
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def save_state(state):
    """Persist tracking state."""
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def format_entry_for_review(entry):
    """Format a DB entry as a readable review block."""
    content = entry.get("content") or ""
    if len(content) > 500:
        content = content[:500] + "..."
    reasoning = entry.get("reasoning") or "None"
    if len(reasoning) > 300:
        reasoning = reasoning[:300] + "..."
    source_url = entry.get("source_url") or "None"
    timestamp = entry.get("timestamp")
    ts_str = ""
    if timestamp:
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        ts_str = dt.strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"ENTRY ID: {entry['entry_id']}",
        f"DISCORD MSG: {entry.get('discord_message_id')}",
        f"TIME: {ts_str}",
        f"AI CATEGORY: {entry.get('original_category', 'unknown')}",
        f"AI REASONING: {reasoning}",
        f"SOURCE URL: {source_url}",
        f"CONTENT:\n{content}",
        "-" * 60,
    ]
    return "\n".join(lines)


async def scan_ignore_entries(db, state):
    """
    Find entries currently in the ignore channel that we haven't evaluated yet.
    Returns list of entry dicts and updates last_scan_ts.
    """
    now = time.time()
    # Only look at entries that arrived after our last scan
    since_ts = state.get("last_scan_ts", 0)
    if since_ts == 0:
        # First run — look back 24 hours to catch the full backlog.
        # The bot may have been paused or down; we want everything since
        # the last time it was actively posting.
        since_ts = now - 86400  # 24 hours back

    # Find entries that need attention:
    # 1. Entries in the ignore channel (category = 'ignore') — need review
    # 2. Entries stranded in the ignore channel with a non-ignore category
    #    (promote failed after DB update left them in limbo — category != 'ignore'
    #     but discord_channel_id still points to the ignore channel)
    # 3. Entries stranded in ANY channel (no Discord message — need recovery)
    # Skip entries we've already evaluated (tracked in state) EXCEPT limbo entries:
    # limbo entries (category != 'ignore' but in ignore channel) are retried on
    # every scan because the promote may have failed due to a transient 404.
    IGNORE_CH_ID = config.DISCORD_CHANNELS.get(config.DEFAULT_CATEGORY)
    rows = db.conn.execute(
        """SELECT * FROM message_mapping
           WHERE (
               -- New ignore entries since last scan
               (category = 'ignore' AND timestamp > ?)
               OR
               -- Limbo entries: non-ignore category but physically still in ignore channel
               (discord_channel_id = ? AND category != 'ignore')
               OR
               -- Stranded entries: no Discord message in ANY channel
               (discord_message_id IS NULL AND timestamp > ?)
           )
           ORDER BY timestamp ASC""",
        (since_ts, IGNORE_CH_ID, since_ts),
    ).fetchall()

    entries = []
    for row in rows:
        entry_id = row["entry_id"]

        # Skip entries we've already evaluated
        if entry_id in state.get("evaluated_entries", []):
            continue

        # Skip entries where the user manually moved them to ignore
        # AND the move actually succeeded (entry has a valid Discord message ID).
        # Stranded entries (category='ignore' but discord_message_id IS NULL) are
        # NOT skipped — they need to be re-posted to the ignore channel.
        placement_reason = row["placement_reason"] if row["placement_reason"] else ""
        if (
            placement_reason.startswith("User re-categorization")
            and row["category"] == "ignore"
            and row["discord_message_id"] is not None
        ):
            # User moved this back to ignore and it has a real message — respect it
            print(f"  SKIP (user-demoted, has message): {entry_id}", file=sys.stderr)
            continue

        entries.append(dict(row))

    # Update state — track evaluated entries, but NOT limbo entries
    # (category != 'ignore' but in ignore channel). Limbo entries need to be
    # retried on every scan in case the promote failure was transient.
    state["last_scan_ts"] = now
    evaluated = state.get("evaluated_entries", [])
    for entry in entries:
        entry_id = entry["entry_id"]
        # Only track as "evaluated" if it's a normal ignore entry or stranded entry.
        # Limbo entries (non-ignore category, still in ignore channel) are retried.
        if entry.get("category") != "ignore" and entry.get("discord_channel_id") == IGNORE_CH_ID:
            continue  # don't add to evaluated — retry next scan
        evaluated.append(entry_id)
    state["evaluated_entries"] = evaluated
    save_state(state)

    return entries


async def promote_entry(poster, db, entry_id, category=None):
    """
    Promote an entry from ignore to the main channel.
    Uses the poster's recategorize_entry() so it posts as NewsBot.
    Falls back to a fresh post_message() if the original Discord message is gone.
    """
    # Look up the Discord message ID and channel for this entry
    info = db.get_discord_message_info(entry_id)
    if not info:
        print(f"ERROR: No DB record found for entry {entry_id}", file=sys.stderr)
        return False

    discord_msg_id = info.get("discord_message_id")
    discord_channel_id = info.get("discord_channel_id")
    content = info.get("content", "")

    if not discord_msg_id or not discord_channel_id:
        print(f"ERROR: Entry {entry_id} has no Discord message mapping", file=sys.stderr)
        return False

    # Use the main category (or specified override)
    target_category = category or MAIN_CATEGORY

    print(f"Promoting {entry_id} → {target_category}...", file=sys.stderr)

    # Try the normal recategorize path first (cross-post + delete original)
    # Pass use_nonce=False — the monitor runs in a fresh Discord session every
    # 10 minutes, so using a nonce derived from entry_id collides with the nonce
    # used when the entry was first posted to ignore in a prior session, causing
    # 404 "Unknown Message" on channel.send(). Skipping the nonce avoids it.
    success, new_msg_id, new_channel_id, error = await poster.recategorize_entry(
        message_id=discord_msg_id,
        channel_id=discord_channel_id,
        new_category=target_category,
        entry_id=entry_id,
        content=content,
        media_files=None,
        video_urls=info.get("video_urls", []),
        source_type=info.get("source_type", "unknown"),
        user=None,  # No user context — system promotion
        user_reason="Promoted by NewsBot Monitor",
        use_nonce=False,
    )

    if success:
        print(f"  SUCCESS: promoted to message {new_msg_id} in channel {new_channel_id}", file=sys.stderr)
        # Clean up the old message in the source channel. recategorize_entry already
        # schedules a deferred delete internally, but we do an explicit delete here as
        # a belt-and-suspenders guarantee — the monitor's Discord client stays running
        # so this will fire immediately, leaving no duplicate behind.
        # Only clean up if the source channel differs from the target — if they're
        # the same (entry already in target channel), recategorize reposted in place
        # and there's nothing to remove.
        if discord_channel_id != new_channel_id:
            try:
                old_channel = poster.client.get_channel(discord_channel_id)
                if old_channel:
                    old_message = await old_channel.fetch_message(discord_msg_id)
                    if old_message:
                        await old_message.delete()
                        print(f"  Cleaned up old message {discord_msg_id} from source channel", file=sys.stderr)
            except discord.NotFound:
                print(f"  Old message {discord_msg_id} already gone — no cleanup needed", file=sys.stderr)
            except Exception as e:
                print(f"  WARNING: could not delete old message {discord_msg_id}: {e}", file=sys.stderr)
        return True

    # recategorize failed — check what actually failed
    if "Original message not found" in error or "not found" in error.lower():
        print(f"  Original message {discord_msg_id} not found — falling back to fresh post...", file=sys.stderr)
        # The original message is gone. Post a fresh message to the target channel
        # instead of trying to move the dead message.
        # Use entry_id=None for the fallback so Discord gives it a fresh nonce —
        # the original post with this entry_id's nonce already failed and may still
        # be in Discord's dedup window, which could cause the fresh post to be
        # incorrectly deduplicated or rejected.
        success2, new_msg_id2, new_channel_id2 = await poster.post_message(
            category=target_category,
            content=content,
            media_files=None,
            video_urls=info.get("video_urls", []),
            source_type=info.get("source_type", "unknown"),
            entry_id=None,  # fresh nonce — avoid dedup conflict with failed cross-post
            display_category=info.get("original_category") or target_category,
            secondary_category=info.get("secondary_category"),
            use_nonce=False,
        )
        if success2:
            print(f"  SUCCESS (fallback): fresh post to message {new_msg_id2} in channel {new_channel_id2}", file=sys.stderr)
            # The original message was already gone (404), so no source cleanup needed.
            return True
        else:
            print(f"  FALLBACK FAILED: {error}", file=sys.stderr)
            return False

    # Check if the failure was a 404 on the cross-post itself (channel.send() 404),
    # not on fetching the original message. channel.send() creates a new message and
    # shouldn't return 404 "Unknown Message" — if we see this, the target channel may
    # have a stale reference or the bot may have lost access.
    # Try a fresh post with a different nonce as a recovery attempt.
    if "404" in error or "Unknown Message" in error:
        print(f"  Cross-post got 404 (possibly stale channel ref) — trying fresh post with new nonce...", file=sys.stderr)
        success2, new_msg_id2, new_channel_id2 = await poster.post_message(
            category=target_category,
            content=content,
            media_files=None,
            video_urls=info.get("video_urls", []),
            source_type=info.get("source_type", "unknown"),
            entry_id=None,  # fresh nonce — the original nonce's post already failed
            display_category=info.get("original_category") or target_category,
            secondary_category=info.get("secondary_category"),
            use_nonce=False,
        )
        if success2:
            print(f"  SUCCESS (fresh-post recovery): message {new_msg_id2} in channel {new_channel_id2}", file=sys.stderr)
            return True
        else:
            print(f"  FRESH POST ALSO FAILED: {error}", file=sys.stderr)
            return False

    # Some other error — give up
    print(f"  FAILED: {error}", file=sys.stderr)
    return False


async def demote_entry(poster, db, entry_id):
    """
    Move an entry back to the ignore channel.
    """
    info = db.get_discord_message_info(entry_id)
    if not info:
        print(f"ERROR: No DB record found for entry {entry_id}", file=sys.stderr)
        return False

    discord_msg_id = info.get("discord_message_id")
    discord_channel_id = info.get("discord_channel_id")
    content = info.get("content", "")

    if not discord_msg_id or not discord_channel_id:
        print(f"ERROR: Entry {entry_id} has no Discord message mapping", file=sys.stderr)
        return False

    print(f"Demoting {entry_id} → ignore...", file=sys.stderr)

    # Pass use_nonce=False — monitor runs in a fresh Discord session every 10 min.
    success, new_msg_id, new_channel_id, error = await poster.recategorize_entry(
        message_id=discord_msg_id,
        channel_id=discord_channel_id,
        new_category=config.DEFAULT_CATEGORY,  # "ignore"
        entry_id=entry_id,
        content=content,
        media_files=None,
        video_urls=info.get("video_urls", []),
        source_type=info.get("source_type", "unknown"),
        user=None,
        user_reason="Demoted by NewsBot Monitor",
        use_nonce=False,
    )

    if success:
        print(f"  SUCCESS: moved to ignore (message {new_msg_id})", file=sys.stderr)
        # Clean up the old message in the source channel, but ONLY if the source
        # channel is different from the target (ignore) channel. If the entry was
        # already in ignore, recategorize_entry reposted it there — there's nothing
        # to clean up and deleting would remove the message we just posted.
        IGNORE_CH_ID = config.DISCORD_CHANNELS.get(config.DEFAULT_CATEGORY)
        if discord_channel_id != IGNORE_CH_ID:
            try:
                old_channel = poster.client.get_channel(discord_channel_id)
                if old_channel:
                    old_message = await old_channel.fetch_message(discord_msg_id)
                    if old_message:
                        await old_message.delete()
                        print(f"  Cleaned up old message {discord_msg_id} from source channel", file=sys.stderr)
            except discord.NotFound:
                print(f"  Old message {discord_msg_id} already gone — no cleanup needed", file=sys.stderr)
            except Exception as e:
                print(f"  WARNING: could not delete old message {discord_msg_id}: {e}", file=sys.stderr)
    else:
        # Check if the failure was a 404 on the cross-post itself (not on fetching the original).
        # Try a fresh post with a different nonce as a recovery attempt.
        if "404" in (error or "") or "Unknown Message" in (error or ""):
            print(f"  Cross-post got 404 — trying fresh post to ignore with new nonce...", file=sys.stderr)
            success2, new_msg_id2, new_ch_id2 = await poster.post_message(
                category=config.DEFAULT_CATEGORY,
                content=content,
                media_files=None,
                video_urls=info.get("video_urls", []),
                source_type=info.get("source_type", "unknown"),
                entry_id=None,  # fresh nonce
                display_category=info.get("original_category") or config.DEFAULT_CATEGORY,
                secondary_category=info.get("secondary_category"),
            )
            if success2:
                print(f"  SUCCESS (fresh-post recovery): message {new_msg_id2} in ignore channel", file=sys.stderr)
                return True
            else:
                print(f"  FRESH POST ALSO FAILED: {error}", file=sys.stderr)
                return False
        print(f"  FAILED: {error}", file=sys.stderr)

    return success


async def run_scan(poster, db):
    """Scan for new ignore entries and decide what to do with each one.

    Two-tier decision:
    1. Hard rules (deterministic, profile-independent):
       - Previously demoted entry ID → hard veto (auto-demote)
       - Polymarket odds appended → auto-demote
       - Newsletter/listicle format → auto-demote
    2. Profile scoring (advisory):
       - Score every entry against your historical promote/demote patterns
       - Sort by promote score so review queue is ordered best-first
       - Do NOT auto-promote from profile scores alone — you still call it

    Every decision (auto or manual) is logged so you can see what happened.
    """
    state = load_state()
    entries = await scan_ignore_entries(db, state)

    if not entries:
        print("No new entries in ignore channel since last scan.")
        return

    # Rebuild feedback profile from DB so it always has the latest user actions
    rebuild_profile_from_db(db.conn)
    profile = load_feedback()

    auto_demoted = []
    needs_review = []
    recovered = []  # stranded entries re-posted to ignore channel

    for entry in entries:
        score = score_entry_against_profile(entry, profile)
        promote_score = score["promote_match_score"]
        demote_score = score["demote_match_score"]
        details = score["match_details"]

        # ── Recovery: stranded entries (no Discord message in ANY channel) ──
        # These are entries whose DB says category=X but have no discord_message_id
        # — they were re-categorized but the repost failed, leaving them with no
        # Discord presence anywhere. Re-post them to their target channel.
        # Use post_message directly (not recategorize_entry) since there's no
        # original message to delete — this is a fresh post.
        if entry.get("discord_message_id") is None:
            target_ch_id = entry.get("discord_channel_id")
            target_cat = entry["category"]
            if target_ch_id is None:
                # Fall back to the channel for this category
                target_ch_id = config.channels.get(target_cat)
            if target_ch_id is None:
                recovered.append((entry, score, f"Recovery SKIPPED: no channel for category '{target_cat}'"))
                continue
            success, new_msg_id, new_ch_id = await poster.post_message(
                category=target_cat,
                content=entry.get("content", ""),
                media_files=None,
                video_urls=entry.get("video_urls", []),
                source_type=entry.get("source_type", "unknown"),
                entry_id=entry["entry_id"],
                display_category=entry.get("original_category") or target_cat,
                secondary_category=entry.get("secondary_category"),
            )
            if success:
                # Update DB with the new Discord message ID
                db.update_message_mapping_fields(
                    entry["entry_id"],
                    discord_message_id=new_msg_id,
                    discord_channel_id=new_ch_id,
                )
                update_profile_from_new_action(
                    db.conn,
                    entry["entry_id"],
                    "promote",  # recovery = restoring presence
                    target_cat,
                    target_cat,
                    entry.get("content", ""),
                )
                recovered.append((entry, score, f"Recovered: reposted to {target_cat} channel"))
            else:
                recovered.append((entry, score, f"Recovery FAILED: post_message returned False for {target_cat}"))
            continue

        # ── Hard rules (deterministic, profile-independent) ──

        # 1. Previously demoted entry ID — hard veto
        if entry["entry_id"] in profile.get("demoted_entry_ids", []):
            success = await demote_entry(poster, db, entry["entry_id"])
            if success:
                update_profile_from_new_action(
                    db.conn,
                    entry["entry_id"],
                    "demote",
                    entry.get("category", "ignore"),
                    "ignore",
                    entry.get("content", ""),
                )
                auto_demoted.append((entry, score, "HARD VETO: previously demoted"))
            else:
                auto_demoted.append((entry, score, "HARD VETO FAILED"))
            continue

        # 2. Polymarket odds appended — auto-demote
        #    Check the score details for "Polymarket" or "odds appended"
        has_polymarket = any(
            "Polymarket" in d or "odds appended" in d.lower()
            for d in details
        )
        #    Also check the raw content for the Polymarket odds pattern
        if not has_polymarket:
            content = entry.get("content", "")
            has_polymarket = bool(
                re.search(r"--\s*(chance|probability|odds|% chance|forecast)", content, re.IGNORECASE)
                and "Polymarket" in content
            )

        if has_polymarket:
            success = await demote_entry(poster, db, entry["entry_id"])
            if success:
                update_profile_from_new_action(
                    db.conn,
                    entry["entry_id"],
                    "demote",
                    entry.get("category", "ignore"),
                    "ignore",
                    entry.get("content", ""),
                )
                auto_demoted.append((entry, score, "Polymarket odds line"))
            else:
                auto_demoted.append((entry, score, "Polymarket demote FAILED"))
            continue

        # 3. Newsletter/listicle format — auto-demote
        is_newsletter = any(
            "newsletter" in d.lower() or "listicle" in d.lower()
            for d in details
        )
        if is_newsletter:
            success = await demote_entry(poster, db, entry["entry_id"])
            if success:
                update_profile_from_new_action(
                    db.conn,
                    entry["entry_id"],
                    "demote",
                    entry.get("category", "ignore"),
                    "ignore",
                    entry.get("content", ""),
                )
                auto_demoted.append((entry, score, "Newsletter/listicle"))
            else:
                auto_demoted.append((entry, score, "Newsletter demote FAILED"))
            continue

        # ── Everything else goes to review ──
        # Sort by promote score descending so best candidates surface first
        needs_review.append((entry, score))

    # Sort review queue by promote score (best first)
    needs_review.sort(key=lambda x: -x[1]["promote_match_score"])

    # ── Report ──

    print(f"\n{'=' * 60}")
    print(f"FOUND {len(entries)} NEW ENTRY(IES) IN IGNORE CHANNEL")
    print(f"{'=' * 60}")

    if auto_demoted:
        print(f"\n── AUTO-DEMOTED ({len(auto_demoted)}) ──")
        for entry, score, reason in auto_demoted:
            print(f"  {entry['entry_id']}")
            print(f"    Reason: {reason}")
            print(f"    Scores: promote {score['promote_match_score']}/100, demote {score['demote_match_score']}/100")
            for detail in score["match_details"]:
                print(f"    → {detail}")
            print()

    if needs_review:
        print(f"── NEEDS YOUR REVIEW ({len(needs_review)}) — sorted by promote score ──")
        print()
        for i, (entry, score) in enumerate(needs_review, 1):
            print(f"--- Entry {i} of {len(needs_review)} ---")
            print(f"ID: {entry['entry_id']}")
            print(f"Source: {entry.get('source_type', 'unknown')}")
            print(f"Category: {entry['category']}")
            print(f"Timestamp: {datetime.fromtimestamp(entry['timestamp'], tz=timezone.utc).isoformat()}")
            print(f"Promote match: {score['promote_match_score']}/100")
            print(f"Demote match:  {score['demote_match_score']}/100")
            if score["match_details"]:
                for detail in score["match_details"]:
                    print(f"  {detail}")
            print()
            print(format_entry_for_review(entry))
            print()

    if recovered:
        print(f"\n── RECOVERED STRANDED ENTRIES ({len(recovered)}) ──")
        for entry, score, reason in recovered:
            print(f"  {entry['entry_id']}")
            print(f"    Result: {reason}")
            print(f"    Scores: promote {score['promote_match_score']}/100, demote {score['demote_match_score']}/100")
            print()

    print(f"\n{'=' * 60}")
    print(f"SUMMARY: {len(auto_demoted)} auto-demoted, {len(recovered)} recovered, {len(needs_review)} need your review")
    print(f'To promote:  .venv/bin/python newsbot_monitor.py promote <ENTRY_ID> --category "{MAIN_CATEGORY}"')
    print(f"To demote:   .venv/bin/python newsbot_monitor.py demote <ENTRY_ID>")
    print(f"Categories:  {', '.join(repr(c) for c in PROMOTE_CATEGORIES)}")
    print(f"{'=' * 60}")


async def check_similarity_before_promote(db, entry_id):
    """
    Check if there's already a similar entry in any channel (including ignore).
    Returns (is_similar, match_info) where match_info is a dict with details.
    """
    import config

    # Get the embedding for this entry
    embedding_row = db.conn.execute(
        "SELECT embedding FROM embeddings WHERE entry_id = ?",
        (entry_id,)
    ).fetchone()

    if not embedding_row:
        # No embedding found, can't check similarity
        return False, None

    embedding = json.loads(embedding_row['embedding'])

    # Find top matches (not just the best, since the best might be itself)
    top_matches = db.find_top_matches(embedding, threshold=config.SIMILARITY_THRESHOLD, limit=5)

    # Skip self-matches and find the first non-self match
    for sim, preview, content, match_id in top_matches:
        if match_id == entry_id:
            continue  # Skip self-match

        match_info = db.get_discord_message_info(match_id) if match_id else None
        match_category = match_info.get('category') if match_info else None

        return True, {
            'similarity': sim,
            'match_preview': preview,
            'match_entry_id': match_id,
            'match_category': match_category,
        }

    return False, None


async def run_promote(poster, db, entry_id, category):
    """Promote a single entry."""
    # Check for similar entries before promoting
    is_similar, match_info = await check_similarity_before_promote(db, entry_id)
    if is_similar:
        match_category = match_info['match_category']
        # Only block on cross-post duplicates — if the similar entry is still in
        # ignore, it's a self-duplicate not yet posted anywhere, so let this one through.
        if match_category != 'ignore':
            print(
                f"SKIP: Entry {entry_id} is similar to existing entry {match_info['match_entry_id']} "
                f"(similarity: {match_info['similarity']:.3f}, category: {match_category}) "
                f"— not promoting to avoid duplicate",
                file=sys.stderr,
            )
            return False

    success = await promote_entry(poster, db, entry_id, category)
    if success:
        # Update feedback profile
        info = db.get_discord_message_info(entry_id)
        if info:
            update_profile_from_new_action(
                db.conn,
                entry_id,
                "promote",
                info.get("category", "unknown"),
                category or config.FALLBACK_CATEGORY,
                info.get("content", ""),
            )
    return success


async def run_demote(poster, db, entry_id):
    """Demote a single entry back to ignore."""
    success = await demote_entry(poster, db, entry_id)
    if success:
        # Update feedback profile
        info = db.get_discord_message_info(entry_id)
        if info:
            # After demotion, the category is 'ignore'. We need the category
            # it was in BEFORE demotion. Check the DB after the operation.
            update_profile_from_new_action(
                db.conn,
                entry_id,
                "demote",
                info.get("category", "ignore"),
                "ignore",
                info.get("content", ""),
            )
    return success


async def run_state():
    """Show current tracking state."""
    state = load_state()
    print(f"State file: {STATE_FILE}")
    print(f"Created: {state.get('created_at', 'unknown')}")
    print(f"Last scan: {datetime.fromtimestamp(state.get('last_scan_ts', 0), tz=timezone.utc).isoformat() if state.get('last_scan_ts') else 'never'}")
    evaluated = state.get("evaluated_entries", [])
    print(f"Evaluated entries: {len(evaluated)}")
    if evaluated:
        # Show last 10
        for eid in evaluated[-10:]:
            print(f"  - {eid}")
    if len(evaluated) > 10:
        print(f"  ... and {len(evaluated) - 10} more")


async def run_clear_state():
    """Reset tracking state."""
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
        print(f"Cleared state file: {STATE_FILE}")
    else:
        print("No state file to clear.")


async def main_async(args):
    """Main entry point — runs the command, connecting to Discord only if needed."""
    needs_discord = args.command in ("scan", "promote", "demote")

    if needs_discord:
        db = Database()

        # Fail fast on unknown entry IDs before opening a Discord session
        if args.command in ("promote", "demote") and not db.get_discord_message_info(args.entry_id):
            print(f"ERROR: No DB record found for entry {args.entry_id}", file=sys.stderr)
            return False

        # Initialize the poster (this also registers commands but we won't sync).
        poster = DiscordPoster(
            database=db,
            removed_entries_db=RemovedEntriesDB(),
            sync_commands=False,  # Skip app command sync — avoids rate limits on every connect
        )
        await poster.start()
        try:
            if args.command == "scan":
                await run_scan(poster, poster.database)
            elif args.command == "promote":
                return await run_promote(poster, poster.database, args.entry_id, args.category)
            elif args.command == "demote":
                return await run_demote(poster, poster.database, args.entry_id)
        finally:
            await poster.stop()
    else:
        # State/clear-state only need the database, not Discord
        if args.command == "state":
            await run_state()
        elif args.command == "clear-state":
            await run_clear_state()
    return True


def main():
    parser = argparse.ArgumentParser(
        description="NewsBot Monitor — evaluate and promote entries from ignore channel"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # scan — find new entries
    scan_parser = subparsers.add_parser("scan", help="Scan ignore channel for new entries")
    scan_parser.add_argument(
        "--since",
        type=float,
        default=None,
        help="Only show entries after this Unix timestamp (for debugging)",
    )

    # promote — move an entry to main channel
    promote_parser = subparsers.add_parser("promote", help="Promote an entry to main channel")
    promote_parser.add_argument("entry_id", help="Entry ID to promote (e.g. twitter_123456)")
    # nargs="+" so unquoted multi-word values (--category us politics) still parse
    promote_parser.add_argument(
        "--category",
        nargs="+",
        default=None,
        metavar="CATEGORY",
        help=(
            f'Target category (default: "{MAIN_CATEGORY}"). One of: '
            + ", ".join(f'"{c}"' for c in PROMOTE_CATEGORIES)
        ),
    )

    # demote — move an entry back to ignore
    demote_parser = subparsers.add_parser("demote", help="Move an entry back to ignore")
    demote_parser.add_argument("entry_id", help="Entry ID to demote")

    # state — show tracking state
    subparsers.add_parser("state", help="Show tracking state")

    # clear-state — reset tracking
    subparsers.add_parser("clear-state", help="Reset tracking state")

    # feedback-status — show the feedback profile summary
    subparsers.add_parser("feedback-status", help="Show the feedback profile summary")

    # rebuild-profile — rebuild the feedback profile from the DB
    subparsers.add_parser("rebuild-profile", help="Rebuild the feedback profile from all user actions in the DB")

    # improve — run self-improvement analysis
    subparsers.add_parser("improve", help="Run deep-dive self-improvement analysis")

    args, extras = parser.parse_known_args()

    if args.command == "promote" and args.category is not None:
        # `--category=us politics` leaves "politics" as a stray extra — fold it back in
        words = args.category + extras
        extras = []
        try:
            args.category = normalize_category(words)
        except ValueError as e:
            promote_parser.error(str(e))

    if extras:
        parser.error(f"unrecognized arguments: {' '.join(extras)}")

    # If --since was passed on scan, inject it into state for this run only
    if args.command == "scan" and args.since:
        state = load_state()
        state["last_scan_ts"] = args.since
        save_state(state)

    if args.command == "feedback-status":
        profile = load_feedback()
        print_profile_summary(profile)
        return

    if args.command == "rebuild-profile":
        db = Database()
        profile = rebuild_profile_from_db(db.conn)
        print_profile_summary(profile)
        print(f"\nProfile rebuilt from {profile['promote_patterns']['total_promotes']} promotes and "
              f"{profile['demote_patterns']['total_demotes']} demotes.")
        return

    if args.command == "improve":
        report = build_report()
        print(report)
        return

    if not asyncio.run(main_async(args)):
        sys.exit(1)


if __name__ == "__main__":
    main()
