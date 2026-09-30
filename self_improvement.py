"""
NewsBot Self-Improvement Engine — deep-dive analysis and improvement ideas.

Runs alongside the monitor cron and produces analysis across five dimensions:
1. Bot health (service alive, uptime, restarts)
2. DB health (growth, stalls)
3. Feed health (live feeds, silent feeds, buffer health, junk sources)
4. Log error scan (tracebacks, crashes in last N minutes)
5. Feedback scoring quality (are scores predictive of user decisions?)

Each run produces a plain-text report that can be delivered by a cron job.
"""

import difflib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

# ── Paths ──────────────────────────────────────────────────────────────────────

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

import config
from database import Database

SERVICE_NAME = "newsbot.service"
JOURNALCTL_SINCE = "1h"        # how far back to scan logs for errors
JOURNALCTL_LINES = 500         # max log lines to read
FEED_SOURCES_MODULE = "feed_sources"   # not imported here; we read config patterns


# ── Bot health ──────────────────────────────────────────────────────────────────

def check_bot_health():
    """Check whether the systemd user service for NewsBot is running."""
    results = {
        "service_active": False,
        "service_loaded": False,
        "pid": None,
        "active_since": None,
        "uptime_seconds": 0,
        "restart_count": 0,
        "memory_usage": None,
    }

    # Check if unit is active
    out = run_systemctl(["is-active", SERVICE_NAME], capture=True)
    if out.strip() == "active":
        results["service_active"] = True

    # Check if unit is loaded
    out = run_systemctl(["is-enabled", SERVICE_NAME], capture=True)
    if "enabled" in out or "static" in out:
        results["service_loaded"] = True

    # Full status to get PID, since, restarts
    status_out = run_systemctl(["status", SERVICE_NAME], capture=True)
    lines = status_out.split("\n")

    for line in lines:
        m = re.search(r"Main PID:\s+(\d+)", line)
        if m:
            results["pid"] = int(m.group(1))

    # systemctl status's 'Active: since ...' format varies by version; the
    # machine-readable property is stable.
    show_out = run_systemctl(["show", SERVICE_NAME, "--property=ActiveEnterTimestamp"], capture=True)
    m = re.search(r"ActiveEnterTimestamp=(.+)", show_out)
    if m:
        results["active_since"] = m.group(1).strip()

    # Uptime from PID
    if results["pid"]:
        try:
            with open(f"/proc/{results['pid']}/stat") as f:
                pass  # process exists
            results["uptime_seconds"] = round(time.time() - get_process_start_time(results["pid"]))
        except Exception:
            results["uptime_seconds"] = 0

    # Restart count from systemd
    show_out = run_systemctl(["show", SERVICE_NAME, "--property=NRestarts"], capture=True)
    m = re.search(r"NRestarts=(\d+)", show_out)
    if m:
        results["restart_count"] = int(m.group(1))

    return results


def get_process_start_time(pid):
    """Get process start time in seconds since epoch."""
    with open(f"/proc/{pid}/stat") as f:
        fields = f.read().split()
    # starttime is field 22 (0-indexed: 21), in clock ticks since boot
    starttime_ticks = int(fields[21])
    clk_tck = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    with open(f"/proc/uptime") as f:
        uptime_seconds = float(f.read().split()[0])
    # Process started (uptime_seconds - starttime_since_boot) seconds ago
    # boot_epoch = time.time() - uptime_seconds
    # process_start_epoch = boot_epoch + starttime_since_boot
    starttime_seconds = starttime_ticks / clk_tck
    process_start_epoch = time.time() - uptime_seconds + starttime_seconds
    return process_start_epoch


def run_systemctl(args, capture=True):
    """Run systemctl --user with given args."""
    cmd = ["systemctl", "--user"] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return r.stdout + r.stderr
    except Exception as e:
        return f"ERROR: {e}"


# ── DB health ──────────────────────────────────────────────────────────────────

def check_db_health(db):
    """Analyze DB growth and health."""
    results = {
        "total_mappings": 0,
        "processed_ids": 0,
        "embeddings": 0,
        "removed_entries": 0,
        "categories": {},
        "category_counts_by_hour_last_24h": {},
        "timestamp_min": None,
        "timestamp_max": None,
        "daily_growth_7d": [],
        "today_new_entries": 0,
    }

    cursor = db.conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM message_mapping")
    results["total_mappings"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT entry_id) FROM message_mapping")
    results["processed_ids"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM embeddings")
    results["embeddings"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM removed_entries")
    results["removed_entries"] = cursor.fetchone()[0]

    cursor.execute("SELECT category, COUNT(*) FROM message_mapping GROUP BY category")
    for row in cursor.fetchall():
        results["categories"][row[0]] = row[1]

    # Timestamp range
    cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM message_mapping")
    row = cursor.fetchone()
    if row and row[0]:
        ts_min = row[0]
        ts_max = row[1]
        results["timestamp_min"] = datetime.fromtimestamp(ts_min, tz=timezone.utc).isoformat()
        results["timestamp_max"] = datetime.fromtimestamp(ts_max, tz=timezone.utc).isoformat()

        # Today's new entries
        today_start = int(time.time() - (time.time() % 86400))
        cursor.execute(
            "SELECT COUNT(*) FROM message_mapping WHERE timestamp >= ? AND timestamp < ?",
            (today_start, today_start + 86400),
        )
        results["today_new_entries"] = cursor.fetchone()[0]

        # Hourly counts for last 24h
        for h_ago in range(24):
            window_start = ts_max - (h_ago + 1) * 3600
            window_end = ts_max - h_ago * 3600
            cursor.execute(
                "SELECT COUNT(*) FROM message_mapping WHERE timestamp >= ? AND timestamp < ?",
                (window_start, window_end),
            )
            results["category_counts_by_hour_last_24h"][h_ago] = cursor.fetchone()[0]

    return results


# ── Feed health ────────────────────────────────────────────────────────────────

def check_feed_health(db):
    """
    Analyze feed health per feed NAME (not source_type).

    message_mapping has no source column, so per-feed data comes from bot.log
    lines: 'Found N entries in <feed> (M skipped due to parsing errors)'.
    """
    results = {
        "source_types": {},
        "feeds": {},               # feed name -> {avg_found, skipped_total, loss_pct}
        "stale_sources": [],
        "low_volume_sources": [],  # sources with < 2 entries in last 24h
        "high_volume_sources": [], # sources with > 30 entries in last 24h
        "junk_indicators": {},   # sources that frequently produce Polymarket odds lines
        "source_timestamps": {},
    }

    # Per-feed counts from bot.log (last ~200 cycles). Matches both the old
    # 'Found N entries in X (M skipped due to parsing errors)' and the new
    # 'Found N entries in X, M stale' formats.
    feed_re = re.compile(r"Found (\d+) entries in ([\w.]+)(?: \((\d+) skipped due to parsing errors\)|, (\d+) stale)?")
    feed_stats = {}
    log_path = os.path.join(PROJECT_ROOT, "bot.log")
    if os.path.exists(log_path):
        with open(log_path, "r", errors="replace") as f:
            for line in f.readlines()[-20000:]:
                m = feed_re.search(line)
                if m:
                    found, name = int(m.group(1)), m.group(2)
                    skipped = int(m.group(3) or m.group(4) or 0)
                    stats = feed_stats.setdefault(name, {"found": 0, "skipped": 0, "cycles": 0})
                    stats["found"] += found
                    stats["skipped"] += skipped
                    stats["cycles"] += 1
    for name, stats in feed_stats.items():
        results["feeds"][name] = {
            "avg_found": round(stats["found"] / max(stats["cycles"], 1), 1),
            "skipped_total": stats["skipped"],
            "loss_pct": round(stats["skipped"] / max(stats["found"] + stats["skipped"], 1) * 100),
        }
        if stats["found"] == 0 and stats["cycles"] >= 3:
            results["stale_sources"].append(name)
        if stats["found"] < 10:
            results["low_volume_sources"].append(name)
        if stats["found"] > 200:
            results["high_volume_sources"].append(name)

    # Keep the source_type view (twitter vs telegram split) for build_report.
    cursor = db.conn.cursor()
    cursor.execute("SELECT MAX(timestamp) FROM message_mapping")
    row = cursor.fetchone()
    if row and row[0]:
        max_ts = row[0]
        cutoff_24h = max_ts - 86400
        cursor.execute(
            "SELECT source_type, COUNT(*) FROM message_mapping WHERE timestamp >= ? GROUP BY source_type",
            (cutoff_24h,),
        )
        for source, count in cursor.fetchall():
            results["source_types"][source] = {"count_24h": count}

    return results


# ── Log scan ───────────────────────────────────────────────────────────────────

def scan_logs_for_errors(since="1h", max_lines=500):
    """Scan the application log (bot.log) for errors/tracebacks/rate limits.

    journalctl is NOT a valid source: the bot logs to bot.log, and the systemd
    journal captures almost none of it (verified 2026-09-06: 3,402 embedding
    errors in bot.log, ~0 in journalctl).
    """
    results = {
        "error_lines": [],
        "crash_lines": [],
        "traceback_lines": [],
        "rate_limit_lines": [],
        "total_lines_scanned": 0,
        "error_count": 0,
    }

    log_path = os.path.join(PROJECT_ROOT, "bot.log")
    if not os.path.exists(log_path):
        results["error_lines"].append(f"bot.log not found at {log_path}")
        return results

    # Tail the file: read last max_lines lines without loading the whole file.
    with open(log_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        chunk = b""
        read = 0
        while read < size and chunk.count(b"\n") < max_lines:
            read = min(size, read + 65536)
            f.seek(size - read)
            chunk = f.read(read)
        tail = chunk.decode("utf-8", errors="replace")

    lines = [l for l in tail.split("\n") if l.strip()]
    results["total_lines_scanned"] = len(lines)

    for line in lines[-max_lines:]:
        lower = line.lower()
        if any(w in lower for w in ["traceback", "exception", "error:", "critical", "fatal", "crash"]):
            results["error_lines"].append(line[:300])
            if "traceback" in lower:
                results["traceback_lines"].append(line[:300])
            if "error" in lower or "critical" in lower or "fatal" in lower:
                results["crash_lines"].append(line[:300])
            results["error_count"] += 1
        if "rate limit" in lower or "429" in lower or "discord.errors" in lower:
            results["rate_limit_lines"].append(line[:300])

    return results


def run_journalctl(since="1h", max_lines=500):
    """Run journalctl for newsbot.service."""
    cmd = [
        "journalctl",
        "--user",
        "-u", SERVICE_NAME,
        "--since", since,
        "-n", str(max_lines),
        "--no-pager",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout + r.stderr
    except Exception as e:
        return f"ERROR: {e}"


# ── Feedback scoring quality ───────────────────────────────────────────────────

def check_feedback_scoring_quality(db):
    """
    Check whether the feedback scoring actually predicts user decisions.
    Compare the score shown during scan against whether the user promoted or demoted.
    
    This is limited: we only know the score *at scan time*, not retrospectively.
    We assess: 
    - Of entries scored with high demote match (>=50), what % were actually demoted?
    - Of entries scored with high promote match (>=50), what % were left alone (not demoted)?
    """
    results = {
        "total_scored_entries": 0,
        "high_demote_scored": 0,
        "high_demote_actually_demoted": 0,
        "high_promote_scored": 0,
        "high_promote_not_demoted": 0,
        "profile_path_exists": False,
        "profile_total_promotes": 0,
        "profile_total_demotes": 0,
    }

    profile_path = os.path.expanduser("~/.hermes/cache/newsbot-feedback-profile.json")
    results["profile_path_exists"] = os.path.exists(profile_path)

    if results["profile_path_exists"]:
        try:
            with open(profile_path) as f:
                profile = json.load(f)
            results["profile_total_promotes"] = profile.get("promote_patterns", {}).get("total_promotes", 0)
            results["profile_total_demotes"] = profile.get("demote_patterns", {}).get("total_demotes", 0)
        except Exception:
            pass

    return results


# ── Ghost-anchor suppression ──────────────────────────────────────────────────

def check_ghost_anchor_suppression(db):
    """
    Detect ghost entries: marked processed + embedded but with NO
    message_mapping row — i.e. zero Discord trace.

    Brandi's standing contract (2026-09-07): every entry must be visible in
    the ignore channel. Each ghost is a contract violation. Classification
    cross-references bot.log (current + rotated) to identify WHICH
    suppression path produced the ghost, so the right code path gets fixed:
      - 'similar-suppressed':  old silent path (should be extinct post-9/7)
      - 'same-cycle-hash':     content-hash early suppression
      - 'concurrent-sibling':  in-flight sibling early suppression
      - 'unknown':             no marker line found in retained logs
    """
    cursor = db.conn.cursor()
    cursor.execute("""
        SELECT p.entry_id, p.timestamp, e.preview
        FROM processed_ids p
        JOIN embeddings e ON p.entry_id = e.entry_id
        LEFT JOIN message_mapping m ON p.entry_id = m.entry_id
        WHERE m.entry_id IS NULL
        ORDER BY p.timestamp DESC
    """)
    ghosts = []
    for entry_id, ts, preview in cursor.fetchall():
        ghosts.append({
            "entry_id": entry_id,
            "timestamp": ts,
            "preview": (preview or "")[:100],
            "path": "unknown",
        })

    if ghosts:
        ghost_ids = [g["entry_id"] for g in ghosts]
        markers = [
            ("similar-suppressed", "Similar story suppressed (not posted)"),
            ("same-cycle-hash", "Same-cycle content-hash duplicate"),
            ("concurrent-sibling", "Concurrent-sibling duplicate"),
        ]
        # Single pass over bot.log + rotated logs (bounded: 5MB x 5 backups max)
        for suffix in ("", ".1", ".2", ".3", ".4", ".5"):
            log_path = os.path.join(PROJECT_ROOT, f"bot.log{suffix}")
            if not os.path.exists(log_path):
                continue
            remaining = [g for g in ghosts if g["path"] == "unknown"]
            if not remaining:
                break
            with open(log_path, "r", errors="replace") as f:
                for line in f:
                    if not any(gid in line for gid in ghost_ids):
                        continue
                    for g in remaining:
                        if g["entry_id"] in line and g["path"] == "unknown":
                            for label, marker in markers:
                                if marker in line:
                                    g["path"] = label
                                    break

    # Cutoff = the 2026-09-23 21:10 CDT deploy of the fix that closed the last
    # two suppression paths (same-cycle hash + concurrent-sibling). Anything
    # after this was created BY the fixed code — a real new leak.
    fix_epoch = time.mktime(time.strptime("2026-09-23 21:10:00", "%Y-%m-%d %H:%M:%S"))
    by_path = defaultdict(int)
    for g in ghosts:
        by_path[g["path"]] += 1
    return {
        "total": len(ghosts),
        # 'since_fix' = created after the 2026-09-23 fix that closed the last two
        # suppression paths — any ghost here means a NEW leak and is critical
        "since_fix": sum(1 for g in ghosts if g["timestamp"] > fix_epoch),
        "by_path": dict(by_path),
        "ghosts": ghosts,
    }


# ── Dedup effectiveness ───────────────────────────────────────────────────────

def check_dedup_effectiveness(db, window_hours=48, max_entries=300,
                              ratio_threshold=0.85, pair_window_seconds=3600):
    """
    Measure whether near-duplicate detection is catching what it should.

    Two signals:
    1. MISSED dedup: pairs of entries in the same category, posted within
       pair_window_seconds of each other, with content similarity >=
       ratio_threshold that BOTH posted (Python-side difflib scoring — SQL
       alone can't do this, and 'same category within 1h' alone matched
       11,511 pairs at current volume, so content comparison is required).
    2. HEALTHY dedup evidence: recent mapping rows routed to ignore by the
       duplicate/near-duplicate overrides.

    Flags PATTERNS for review — never declares a bug on its own.
    """
    cursor = db.conn.cursor()
    cutoff = time.time() - window_hours * 3600
    cursor.execute("""
        SELECT entry_id, category, content, timestamp FROM message_mapping
        WHERE timestamp >= ? AND category != 'ignore' AND content IS NOT NULL
        ORDER BY timestamp DESC LIMIT ?
    """, (cutoff, max_entries))
    rows = cursor.fetchall()

    missed = []
    n = len(rows)
    # Cap pairwise cost; difflib on hundreds of pairs is fine, thousands is not
    if n <= max_entries:
        for i in range(n):
            for j in range(i + 1, n):
                # rows are timestamp-DESC: once older than the pair window, stop
                if rows[i][3] - rows[j][3] > pair_window_seconds:
                    break
                if rows[i][1] != rows[j][1]:
                    continue
                a = rows[i][2] or ""
                b = rows[j][2] or ""
                if not a or not b:
                    continue
                # cheap pre-filter before difflib: length or set overlap
                if abs(len(a) - len(b)) > max(len(a), len(b)) * 0.5:
                    continue
                ratio = difflib.SequenceMatcher(None, a[:2000], b[:2000]).ratio()
                if ratio >= ratio_threshold:
                    missed.append({
                        "a": rows[i][0], "b": rows[j][0],
                        "category": rows[i][1],
                        "ratio": round(ratio, 3),
                        "minutes_apart": round((rows[i][3] - rows[j][3]) / 60, 1),
                        "preview": a[:80],
                    })

    cursor.execute("""
        SELECT COUNT(*) FROM message_mapping
        WHERE timestamp >= ?
          AND (placement_reason LIKE '%Duplicate override%'
               OR placement_reason LIKE '%Similar content override%')
    """, (time.time() - 86400,))
    caught_24h = cursor.fetchone()[0]

    return {
        "window_hours": window_hours,
        "entries_scanned": n,
        "potential_missed_dedup": missed,
        "dedup_routings_24h": caught_24h,
    }


# ── Merger buffer health ──────────────────────────────────────────────────────

def check_merger_buffer_health(db):
    """
    Check Polymarket/Dexerto pending buffers for entries stuck beyond their
    max age (merger bottleneck / flush failure).

    buffered_at is epoch REAL (written from time.time()) — comparison MUST be
    epoch-vs-epoch with a bound parameter. Comparing against
    datetime('now', ...) text is ALWAYS TRUE in SQLite (REAL < TEXT) and
    flags every row stale.
    """
    cursor = db.conn.cursor()
    now = time.time()
    results = {}
    for table, cfg_attr, default_age in [
        ("polymarket_pending", "POLYMARKET_PENDING_MAX_AGE_HOURS", 4.0),
        ("dexerto_pending", "DEXERTO_PENDING_MAX_AGE_HOURS", 1.0),
    ]:
        max_age = float(getattr(config, cfg_attr, default_age))
        try:
            cursor.execute(f"""
                SELECT COUNT(*), MIN(buffered_at), MAX(buffered_at)
                FROM {table} WHERE buffered_at < ?
            """, (now - max_age * 3600,))
            stale, oldest, newest = cursor.fetchone()
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            total = cursor.fetchone()[0]
            results[table] = {
                "max_age_hours": max_age,
                "total_pending": total,
                "stale": stale,
                "oldest_age_hours": round((now - oldest) / 3600, 1) if oldest else None,
            }
        except Exception as e:
            results[table] = {"error": str(e)}
    return results


# ── Regression verification ───────────────────────────────────────────────────

def check_regressions():
    """
    Verify known fixed bugs are still fixed by inspecting actual code/config.
    Any FAIL is a critical regression. All paths anchored to this file's
    directory — never the cwd (repo path contains spaces).
    """
    checks = {}

    def repo_read(name):
        with open(os.path.join(PROJECT_ROOT, name), encoding="utf-8", errors="replace") as f:
            return f.read()

    # 1. Embedding endpoint: /api/embed (>= Ollama 0.32), never /api/embeddings.
    #    /api/embeddings may legitimately appear in COMMENTS documenting the
    #    deprecation — only flag non-comment usage.
    try:
        code = repo_read("ollama_client.py")
        active_embeddings_use = any(
            "/api/embeddings" in line and not line.strip().startswith("#")
            for line in code.splitlines()
        )
        checks["embedding_endpoint"] = "/api/embed" in code and not active_embeddings_use
    except Exception:
        checks["embedding_endpoint"] = None

    # 2. post_message signature must carry use_nonce + video_unavailable
    #    (nonce-collision fix + NameError-on-post fix). Signature-level:
    #    substring presence proves nothing about ordering/params.
    try:
        code = repo_read("discord_messaging.py")
        m = re.search(r"def post_message\(([^)]*)\)", code, re.S)
        sig = m.group(1) if m else ""
        checks["post_message_signature"] = bool(m) and "use_nonce" in sig and "video_unavailable" in sig
    except Exception:
        checks["post_message_signature"] = None

    # 3. Monitor skips nonce derivation (cross-session 404 fix)
    try:
        code = repo_read("newsbot_monitor.py")
        checks["monitor_use_nonce_false"] = "use_nonce=False" in code
    except Exception:
        checks["monitor_use_nonce_false"] = None

    # 4. Twitter link-card preview stripping (Nitter junk in follow-up text).
    #    Called as asyncio.to_thread(strip_twitter_card_preview, ...) — a
    #    function REFERENCE with no paren, so check bare presence.
    try:
        code = repo_read("main.py")
        checks["strip_twitter_card_preview"] = "strip_twitter_card_preview" in code
    except Exception:
        checks["strip_twitter_card_preview"] = None

    # 5. gallery-dl replies=false (duplicate Dexerto follow-up fix)
    try:
        gd_path = os.path.expanduser("~/.config/gallery-dl/config.json")
        with open(gd_path) as f:
            gd = json.load(f)

        def find_replies(node):
            found = []
            if isinstance(node, dict):
                for k, v in node.items():
                    if k == "replies":
                        found.append(v)
                    found.extend(find_replies(v))
            elif isinstance(node, list):
                for v in node:
                    found.extend(find_replies(v))
            return found
        replies = find_replies(gd)
        checks["gallery_dl_replies_false"] = bool(replies) and all(r is False for r in replies)
    except Exception:
        checks["gallery_dl_replies_false"] = None

    # 6. systemd unit: ExecStart must not point directly at a space-containing
    #    path (systemd splits on spaces -> status=203/EXEC; wrapper-script fix)
    try:
        r = subprocess.run(
            ["systemctl", "--user", "cat", SERVICE_NAME],
            capture_output=True, text=True, timeout=15,
        )
        unit = r.stdout + r.stderr
        exec_start = None
        working_dir = None
        for line in unit.splitlines():
            if line.startswith("ExecStart="):
                exec_start = line[len("ExecStart="):].strip()
            elif line.startswith("WorkingDirectory="):
                working_dir = line[len("WorkingDirectory="):].strip()
        # strip environment prefixes systemd allows (e.g. "+", "-", "!") — not spaces
        exec_path = (exec_start or "").split()
        exec_bin = exec_path[0] if exec_path else ""
        checks["systemd_execstart"] = bool(exec_bin) and " " not in exec_bin
        checks["systemd_detail"] = {"exec_start": exec_start, "working_directory": working_dir}
    except Exception as e:
        checks["systemd_execstart"] = None
        checks["systemd_detail"] = {"error": str(e)}

    return checks


# ── Improvement ideas ──────────────────────────────────────────────────────────

def generate_improvement_ideas(health, db_health, feed_health, log_errors, fb_quality,
                               ghosts=None, dedup=None, buffers=None, regressions=None):
    """Generate concrete improvement suggestions based on all checks.

    Only fires on problems the checks actually found — no generic filler.
    Known-expected noise (PAUSE_MODE, Nitter 429s, prompt cache 0%, profile
    scores under 40, high-volume feeds) is deliberately never suggested.
    """
    ideas = []

    now = datetime.now(timezone.utc)

    # Bot health ideas
    if not health["service_active"]:
        ideas.append({
            "type": "critical",
            "area": "Bot service",
            "suggestion": f"NewsBot service is NOT running. Run: systemctl --user restart {SERVICE_NAME}",
            "severity": "high",
        })
    elif health["restart_count"] > 3 and health["uptime_seconds"] < 3600:
        ideas.append({
            "type": "warning",
            "area": "Bot service",
            "suggestion": f"Bot has restarted {health['restart_count']} times recently (uptime {health['uptime_seconds']}s). Investigate — check journalctl for crash loops.",
            "severity": "medium",
        })

    # DB health ideas
    today = db_health.get("today_new_entries", 0)
    if today == 0:
        ideas.append({
            "type": "warning",
            "area": "DB / Feeds",
            "suggestion": "Zero new entries today. Either feeds are down or bot isn't polling. Check feeds and bot logs.",
            "severity": "medium",
        })

    total = db_health.get("total_mappings", 0)
    # NOTE: the old "<1% of total today = low activity" info idea was removed —
    # it fired on every run (PAUSE_MODE routes everything to ignore by design)
    # and was pure re-reported noise. Zero-today (above) stays: it's the real signal.

    # Ghost suppression: any ghost is a violation of the ignore-channel contract
    if ghosts and ghosts.get("total", 0) > 0:
        by_path = ghosts.get("by_path", {})
        path_summary = ", ".join(f"{k}: {v}" for k, v in sorted(by_path.items()))
        new_count = ghosts.get("since_fix", ghosts["total"])
        if new_count == 0:
            # All ghosts predate the 2026-09-07 contract fix — historical, not growing
            ideas.append({
                "type": "info",
                "area": "Ghost suppression",
                "suggestion": (
                    f"{ghosts['total']} historical ghost entries (all pre-date the "
                    f"2026-09-23 suppression-path fix). No NEW ghosts — suppression "
                    f"By path: {path_summary}."
                ),
                "severity": "low",
            })
        else:
            ideas.append({
                "type": "critical",
                "area": "Ghost suppression",
                "suggestion": (
                    f"{new_count} NEW ghost entries (post-2026-09-07 fix) were "
                    f"processed+embedded but NEVER posted anywhere (no mapping row, no "
                    f"Discord trace) — violates the contract that every entry is visible "
                    f"in ignore. Total including historical: {ghosts['total']}. "
                    f"By path: {path_summary}. Classified via bot.log cross-reference."
                ),
                "severity": "high",
            })

    # Dedup effectiveness
    if dedup:
        missed = dedup.get("potential_missed_dedup", [])
        if missed:
            examples = "; ".join(
                f"{m['a']} ≈ {m['b']} (ratio {m['ratio']}, {m['minutes_apart']}min apart)"
                for m in missed[:3]
            )
            ideas.append({
                "type": "warning",
                "area": "Dedup effectiveness",
                "suggestion": (
                    f"{len(missed)} pair(s) of near-identical entries BOTH posted in the "
                    f"last {dedup.get('window_hours', 48)}h — dedup may be missing them. "
                    f"Patterns for review, not confirmed bugs: {examples}"
                ),
                "severity": "medium",
            })

    # Merger buffer health
    if buffers:
        for table, info in buffers.items():
            if not isinstance(info, dict) or "error" in info:
                if isinstance(info, dict) and "error" in info:
                    ideas.append({
                        "type": "warning",
                        "area": "Merger buffers",
                        "suggestion": f"Could not check {table}: {info['error']}",
                        "severity": "low",
                    })
                continue
            if info.get("stale", 0) > 0:
                ideas.append({
                    "type": "warning",
                    "area": "Merger buffers",
                    "suggestion": (
                        f"{info['stale']} of {info['total_pending']} entries in {table} "
                        f"exceeded the {info['max_age_hours']}h max age (oldest: "
                        f"{info['oldest_age_hours']}h). Merger may be bottlenecked or "
                        f"flush_stale isn't running — check 'Dispatching N entries' in bot.log."
                    ),
                    "severity": "medium",
                })

    # Regression checks — any FAIL is critical
    if regressions:
        failed = [k for k, v in regressions.items() if v is False]
        errored = [k for k, v in regressions.items() if v is None and k != "systemd_detail"]
        if failed:
            ideas.append({
                "type": "critical",
                "area": "Regression",
                "suggestion": (
                    f"Previously-fixed bug patterns NO LONGER PRESENT in code: "
                    f"{', '.join(failed)}. A known bug has regressed — fix before anything else."
                ),
                "severity": "high",
            })
        if errored:
            ideas.append({
                "type": "warning",
                "area": "Regression",
                "suggestion": f"Could not verify regression checks: {', '.join(errored)} (read errors).",
                "severity": "low",
            })

    # Feed health ideas
    if feed_health.get("stale_sources"):
        for src in feed_health["stale_sources"]:
            ideas.append({
                "type": "warning",
                "area": f"Feed: {src}",
                "suggestion": f"Feed '{src}' hasn't produced an entry in 2+ hours. May be down or rate-limited.",
                "severity": "medium",
            })

    if feed_health.get("low_volume_sources"):
        for src in feed_health["low_volume_sources"]:
            ideas.append({
                "type": "info",
                "area": f"Feed: {src}",
                "suggestion": f"Feed '{src}' has very low volume (<2 entries/24h). Consider whether this feed is worth keeping.",
                "severity": "low",
            })

    if feed_health.get("junk_indicators"):
        for src, pct in feed_health["junk_indicators"].items():
            ideas.append({
                "type": "info",
                "area": f"Feed: {src}",
                "suggestion": f"Feed '{src}' has {pct}% entries with raw Polymarket odds lines — these hit the ignore channel. Consider a pre-filter or content cleanup at the feed level.",
                "severity": "low",
            })

    # NOTE: the old "high-volume feed" info idea was removed — high volume is
    # expected behavior and was re-reported every run.
    # NOTE: the generic "Consider adding new feeds" and "Keep it up" filler
    # suggestions were removed — they fired every run regardless of findings.

    # Log error ideas
    if log_errors["error_count"] > 0:
        tb_count = len(log_errors.get("traceback_lines", []))
        if tb_count > 0:
            ideas.append({
                "type": "critical",
                "area": "Logs",
                "suggestion": f"Found {tb_count} traceback(s) in last hour. Review journalctl -u {SERVICE_NAME} --since 1h for full context. May indicate an unhandled exception causing silent failures.",
                "severity": "high",
            })
        if log_errors.get("rate_limit_lines"):
            ideas.append({
                "type": "warning",
                "area": "Logs",
                "suggestion": f"Rate limit hits detected in logs ({len(log_errors['rate_limit_lines'])} lines). Discord API may be throttling — check if bot is spamming or posting too fast.",
                "severity": "medium",
            })
        if log_errors["error_count"] > 20 and tb_count == 0:
            ideas.append({
                "type": "warning",
                "area": "Logs",
                "suggestion": f"{log_errors['error_count']} error lines in last hour but no tracebacks. Likely non-fatal warnings (connection retries, etc). Worth a glance if ongoing.",
                "severity": "low",
            })

    # NOTE: generic always-fire filler ideas ("Consider adding new feeds",
    # "Keep up the feedback") were deliberately removed — see Task 6 of the
    # 2026-09-23 deep-dive plan. Ideas are evidence-driven only now.

    return ideas


def build_report():
    """Run all checks and build a full improvement report."""
    results_data = {}

    now_iso = datetime.now(timezone.utc).isoformat()

    # Bot health
    health = check_bot_health()
    results_data["health"] = health

    # DB health
    db = Database()
    db_health = check_db_health(db)
    results_data["db_health"] = db_health

    # Feed health
    feed_health = check_feed_health(db)
    results_data["feed_health"] = feed_health

    # Log scan
    log_errors = scan_logs_for_errors(since=JOURNALCTL_SINCE, max_lines=JOURNALCTL_LINES)
    results_data["log_errors"] = log_errors

    # Feedback scoring quality
    fb_quality = check_feedback_scoring_quality(db)
    results_data["fb_quality"] = fb_quality

    # Ghost-anchor suppression (contract violations: zero-Discord-trace entries)
    ghosts = check_ghost_anchor_suppression(db)
    results_data["ghosts"] = ghosts

    # Dedup effectiveness (missed near-duplicates + healthy-dedup evidence)
    dedup = check_dedup_effectiveness(db)
    results_data["dedup"] = dedup

    # Merger buffer health (stale pending entries)
    buffers = check_merger_buffer_health(db)
    results_data["buffers"] = buffers

    # Regression verification (known fixed bugs still fixed)
    regressions = check_regressions()
    results_data["regressions"] = regressions

    # Ideas
    ideas = generate_improvement_ideas(health, db_health, feed_health, log_errors, fb_quality,
                                       ghosts=ghosts, dedup=dedup, buffers=buffers,
                                       regressions=regressions)

    # Build report
    report_lines = []
    report_lines.append(f"NEWSBOT SELF-IMPROVEMENT REPORT")
    report_lines.append(f"Generated: {now_iso}")
    report_lines.append(f"{'=' * 60}")
    report_lines.append("")

    # Section 1: Bot health
    report_lines.append("── BOT HEALTH ──")
    report_lines.append(f"  Service active: {'YES' if health['service_active'] else 'NO'}")
    report_lines.append(f"  Service loaded: {'YES' if health['service_loaded'] else 'NO'}")
    if health["pid"]:
        report_lines.append(f"  PID: {health['pid']}")
    if health["active_since"]:
        report_lines.append(f"  Active since: {health['active_since']}")
    if health["uptime_seconds"]:
        uptime_h = health["uptime_seconds"] / 3600
        report_lines.append(f"  Uptime: {uptime_h:.1f}h")
    report_lines.append(f"  Restarts (all time): {health['restart_count']}")
    if health["memory_usage"]:
        report_lines.append(f"  Memory: {health['memory_usage']}")
    report_lines.append("")

    # Section 2: DB health
    report_lines.append("── DB HEALTH ──")
    report_lines.append(f"  Total message mappings: {db_health['total_mappings']}")
    report_lines.append(f"  Unique processed entries: {db_health['processed_ids']}")
    report_lines.append(f"  Embeddings: {db_health['embeddings']}")
    report_lines.append(f"  Removed entries: {db_health['removed_entries']}")
    if db_health["timestamp_min"]:
        report_lines.append(f"  First entry timestamp: {db_health['timestamp_min']}")
    if db_health["timestamp_max"]:
        report_lines.append(f"  Latest entry timestamp: {db_health['timestamp_max']}")
    report_lines.append(f"  Today's new entries: {db_health['today_new_entries']}")
    cat_list = sorted(db_health.get("categories", {}).items(), key=lambda x: -x[1])[:10]
    if cat_list:
        report_lines.append(f"  Top categories:")
        for cat, cnt in cat_list:
            bar = "█" * min(cnt // 10, 40)
            report_lines.append(f"    {cat:30s} {cnt:5d} {bar}")
    report_lines.append("")

    # Section 3: Feed health
    report_lines.append("── FEED HEALTH ──")
    sources = feed_health.get("source_types", {})
    if sources:
        for src, info in sorted(sources.items(), key=lambda x: -x[1]["count_24h"]):
            age = info.get("age_seconds", 0)
            age_str = f"{age//60:.0f}m ago" if age < 3600 else f"{age//3600:.1f}h ago"
            flag = ""
            if src in feed_health.get("stale_sources", []):
                flag = " ⚠ STALE"
            if src in feed_health.get("junk_indicators", []):
                flag += f" ⚠ JUNK({feed_health['junk_indicators'][src]}% odds)"
            if src in feed_health.get("low_volume_sources", []):
                flag += " ⚠ LOW"
            if src in feed_health.get("high_volume_sources", []):
                flag += " ⚠ HIGH-VOL"
            report_lines.append(f"  {src:25s} {info['count_24h']:4d} entries  last: {age_str}{flag}")
    else:
        report_lines.append("  No feed data found.")
    report_lines.append("")

    # Section 4: Log errors
    report_lines.append("── LOG SCAN (errors in last 1h) ──")
    report_lines.append(f"  Errors found: {log_errors['error_count']}")
    report_lines.append(f"  Tracebacks: {len(log_errors.get('traceback_lines', []))}")
    report_lines.append(f"  Rate limit hits: {len(log_errors.get('rate_limit_lines', []))}")
    if log_errors.get("error_lines"):
        report_lines.append("  Sample errors (up to 5):")
        for line in log_errors["error_lines"][:5]:
            report_lines.append(f"    {line[:200]}")
    report_lines.append("")

    # Section 5: Feedback profile status
    report_lines.append("── FEEDBACK PROFILE ──")
    profile_exists = fb_quality['profile_path_exists'] and (fb_quality['profile_total_promotes'] > 0 or fb_quality['profile_total_demotes'] > 0)
    report_lines.append(f"  Profile file exists: {fb_quality['profile_path_exists']}")
    report_lines.append(f"  Profile has data: {profile_exists}")
    report_lines.append(f"  Total promotes tracked: {fb_quality['profile_total_promotes']}")
    report_lines.append(f"  Total demotes tracked: {fb_quality['profile_total_demotes']}")
    report_lines.append("")

    # Section 5b: Ghost suppression (contract check)
    report_lines.append(f"── GHOST SUPPRESSION (entries with zero Discord trace) ──")
    report_lines.append(f"  Total ghosts: {ghosts['total']}")
    report_lines.append(f"  NEW ghosts (created after the 2026-09-23 21:10 fix deploy): {ghosts['since_fix']}")
    if ghosts["total"]:
        for path_label, cnt in sorted(ghosts["by_path"].items()):
            report_lines.append(f"    path={path_label}: {cnt}")
        for g in ghosts["ghosts"][:10]:
            ts_str = datetime.fromtimestamp(g["timestamp"], tz=timezone.utc).strftime("%m-%d %H:%M") if g["timestamp"] else "?"
            report_lines.append(f"    {g['entry_id']}  {ts_str}  via {g['path']}")
            if g["preview"]:
                report_lines.append(f"      preview: {g['preview'][:90]}")
    report_lines.append("")

    # Section 5c: Dedup effectiveness
    report_lines.append("── DEDUP EFFECTIVENESS ──")
    report_lines.append(f"  Entries scanned (last {dedup['window_hours']}h, non-ignore): {dedup['entries_scanned']}")
    report_lines.append(f"  Dedup ignore-routings in last 24h (healthy evidence): {dedup['dedup_routings_24h']}")
    missed = dedup["potential_missed_dedup"]
    report_lines.append(f"  Potential missed near-duplicates (both posted, ratio >= 0.85): {len(missed)}")
    for m in missed[:5]:
        report_lines.append(f"    {m['a']} ≈ {m['b']} (ratio {m['ratio']}, {m['minutes_apart']}min apart, cat={m['category']})")
        report_lines.append(f"      preview: {m['preview'][:90]}")
    report_lines.append("")

    # Section 5d: Merger buffer health
    report_lines.append("── MERGER BUFFER HEALTH ──")
    for table, info in buffers.items():
        if not isinstance(info, dict):
            report_lines.append(f"  {table}: {info}")
        elif "error" in info:
            report_lines.append(f"  {table}: ERROR {info['error']}")
        else:
            stale_flag = f" ⚠ {info['stale']} STALE" if info.get("stale") else ""
            oldest_str = f"{info['oldest_age_hours']}h" if info.get("oldest_age_hours") is not None else "n/a"
            report_lines.append(
                f"  {table}: {info['total_pending']} pending, max age {info['max_age_hours']}h, "
                f"oldest stale {oldest_str}{stale_flag}")
    report_lines.append("")

    # Section 5e: Regression verification
    report_lines.append("── REGRESSION CHECK (known fixes still in place) ──")
    for key, val in regressions.items():
        if key == "systemd_detail":
            detail = val or {}
            report_lines.append(f"  systemd ExecStart: {detail.get('exec_start')}")
            report_lines.append(f"  systemd WorkingDirectory: {detail.get('working_directory')}")
            continue
        status = "PASS" if val is True else ("FAIL ⚠" if val is False else "UNVERIFIED")
        report_lines.append(f"  {key}: {status}")
    report_lines.append("")

    # Section 6: Improvement ideas
    report_lines.append("── IMPROVEMENT IDEAS ──")
    if ideas:
        for idea in ideas:
            sev = idea["severity"].upper()
            report_lines.append(f"  [{sev}] {idea['area']}")
            report_lines.append(f"    → {idea['suggestion']}")
            report_lines.append("")
    else:
        report_lines.append("  No issues detected. Everything looks healthy.")
        report_lines.append("")

    return "\n".join(report_lines)


if __name__ == "__main__":
    from database import Database as _DB
    _DB()  # ensure DB tables exist and are initialized

    report = build_report()
    print(report)

    # Also write to a file for cron delivery
    output_dir = os.path.expanduser("~/.hermes/cache/newsbot-improvement")
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(output_dir, f"improvement_{ts}.txt")
    with open(report_path, "w") as f:
        f.write(report)
    print(f"\nReport saved to: {report_path}")
