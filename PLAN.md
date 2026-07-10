# Automation Plan: Gradual Enablement

## Status Log

Each iteration session: run `python accuracy_report.py` first, append a line here after making a change.

| Date | Change | Metrics at time of change |
|---|---|---|
| 2026-07-09 | Phase 1 enabled: FEEDBACK_LEARNING, IGNORE_EXAMPLES, CORRECTION_EXAMPLES, IGNORE_RESCUE all set to True. Built `accuracy_report.py`. Awaiting service restart to take effect. | 7-day baseline: politics 99.6% (223 reviewed), AI 97.4% (38), sports 97.0% (33), stocks 91.7%, crypto 92.3%, general news 83.1%. Ignore precision 72% (67 rescues / 240). Top confusion: general news→politics (9). |
| 2026-07-09 | Automated the accuracy report: bot now posts it to the ignore channel weekly (ACCURACY_REPORT_* settings in config.py, default: every 7 days at 9 AM local). Last-posted time persists in `data/accuracy_report_state.txt`. | Same baseline as above. |
| 2026-07-10 | **Phases 2+3 enabled (full automation).** Fixed `think:False` being silently ignored (was inside Ollama `options`; qwen3 burned its whole token budget thinking — the newsworthiness rater had NEVER produced real output). Rewrote the rater as a reaction-worthiness gate (surprise/impact/talkability) and calibrated it against 30 days of user move/leave decisions (`evals/calibrate_reaction_gate.py`, 480 samples). Pipeline reordered so the gate runs before pause/graduation routing and its score persists to `message_mapping.newsworthiness_score`. Added: gate rescue for AI-ignored entries (threshold 7.5), AUTO_POST_CATEGORIES lever (None = all graduated), flood guard (25 posts/hour), gate section in the weekly accuracy report. `PAUSE_MODE = False`. | 7-day: politics 100% (222 reviewed), AI 97.8%, sports 97.2%, crypto 94.9%, stocks 90%. Calibration at threshold 6.0: passes 71% of user-approved, blocks 47% of user-left (labels noisy — many "left" entries were skipped duplicates, not boring). Rescue at 7.5: ~60% precision, low volume. |

## Current State (updated 2026-07-10)

**The bot is auto-posting.** `PAUSE_MODE = False`, all categories graduated (`AUTO_POST_CATEGORIES = None`). The decision chain per entry:

1. Categorizer assigns a category (feedback-enhanced prompt, Phase 1 learning live).
2. Duplicate/similarity suppression as before.
3. **Reaction-worthiness gate** (`rate_newsworthiness`): real-category entries scoring below `NEWSWORTHINESS_THRESHOLD` (6.0) are demoted to ignore; AI-ignored entries scoring ≥ `RESCUE_NEWSWORTHINESS_THRESHOLD` (7.5) are re-categorized and rescued. Every score persists in `message_mapping.newsworthiness_score`.
4. Short-video and audience-question filters as before.
5. Routing: pause kill switch → graduation allowlist → flood guard (`MAX_AUTO_POSTS_PER_HOUR`).

Manual moves are now the exception, not the workflow — and each one still feeds the learning loop and the weekly report's REACTION GATE section, which re-derives the best threshold from post-automation data (demotes/rescues are much cleaner labels than pause-mode move/leave).

**Rollback levers** (strongest first): `PAUSE_MODE = True` (everything back to review); `AUTO_POST_CATEGORIES = [...]` (only listed categories post); raise `NEWSWORTHINESS_THRESHOLD` (stricter gate); `HIGH_SCORE_RESCUE_ENABLED = False` (no rescues).

| Toggle | Status | Effect |
|---|---|---|
| `PAUSE_MODE` | OFF | Entries auto-post through the filter chain |
| `AUTO_POST_CATEGORIES` | None (all) | Every category graduated |
| `FEEDBACK_LEARNING_ENABLED` | ON | Removed entries used as negative examples |
| `IGNORE_EXAMPLES_ENABLED` | ON | Ignore-channel entries used as negative examples |
| `CORRECTION_EXAMPLES_ENABLED` | ON | User re-categorizations fed back to AI |
| `IGNORE_RESCUE_ENABLED` | ON | Ignore→category rescues fed back to AI |
| `HIGH_SCORE_RESCUE_ENABLED` | ON | Gate rescues buried-but-interesting AI ignores |
| `SUPERSEDE_ENABLED` | OFF | No auto-replacement of old entries (Phase 4, next) |
| `TRANSCRIPTION_ENABLED` | OFF | No audio transcription from videos (Phase 5) |

---

## The Plan: 5 Phases

### Phase 1 — Enable Feedback Learning (Zero Risk)

**What:** Turn on all four feedback learning toggles. These only affect the system prompt sent to Ollama — they do NOT change what gets posted. Since PAUSE_MODE is still on, everything still goes to ignore.

**Why first:** The AI needs to see your corrections to improve. Right now every manual move/re-categorization you make is recorded in the DB but never read by the categorizer. You're training a model that isn't learning.

**Changes:**
```python
# config.py
FEEDBACK_LEARNING_ENABLED = True
IGNORE_EXAMPLES_ENABLED = True
CORRECTION_EXAMPLES_ENABLED = True
IGNORE_RESCUE_ENABLED = True
```

**What this does:**
- The system prompt now includes: recently removed entries (negative examples), recent ignore-channel entries (negative examples), category-correction pairs (AI said X → you changed to Y), and ignore-rescue pairs (AI said ignore → you promoted to real category).
- Categorization quality improves because the AI sees what it got wrong.
- The nightly prompt cache rebuild at 3 AM picks up the day's corrections.

**How to verify:** After a few days, check the logs for lines like `"Enhanced system prompt with N correction patterns"` and observe whether the AI's category choices in the ignore channel start matching your judgment better.

**Risk: None.** PAUSE_MODE is still on. Everything still goes to ignore.

---

### Phase 2 — Category Graduation (Low Risk)

**What:** Replace the binary `PAUSE_MODE` with a per-category allowlist. Categories you trust auto-post; everything else still goes to ignore.

**Why:** You've been reviewing entries for a while. You probably have a sense of which categories the AI handles well (e.g., "crypto" and "stocks" might be straightforward) vs. which ones it struggles with (e.g., "general news" vs. "politics" boundary).

**New config:**
```python
# config.py
PAUSE_MODE = True  # Master kill switch — when True, AUTO_POST_CATEGORIES is ignored

# Categories that are allowed to auto-post when PAUSE_MODE is False.
# When PAUSE_MODE is True, this is ignored and everything goes to ignore.
# Empty list = manual review for everything (current behavior).
AUTO_POST_CATEGORIES = [
    # "crypto",
    # "stocks",
    # "artificial intelligence",
]
```

**Code changes (main.py, in `process_entry`):**
```python
# Replace the current PAUSE_MODE check (lines 360-369) with:
if getattr(config, 'PAUSE_MODE', False):
    # ... existing pause mode logic routes everything to ignore ...
elif category not in getattr(config, 'AUTO_POST_CATEGORIES', []):
    # Not paused, but this category isn't graduated yet → still review
    original_category = category
    category = 'ignore'
    reasoning = (
        f"AI suggested '{original_category}': {reasoning or 'no reasoning provided'} "
        f"| OVERRIDDEN: category not in AUTO_POST_CATEGORIES, routed to ignore for review"
    )
```

**How to use:**
1. Keep PAUSE_MODE = True for now (Phase 1 is still baking).
2. After a week or two of Phase 1, review the ignore channel. Are there categories where the AI is consistently right?
3. Add one category to AUTO_POST_CATEGORIES, set PAUSE_MODE = False.
4. Watch that category's channel for a day. Any bad posts?
5. Graduate one category at a time.

**Risk: Low.** Only the categories you explicitly trust auto-post. You can always flip PAUSE_MODE back to True as an emergency kill switch.

---

### Phase 3 — Full Auto-Post (Medium Risk)

**What:** Once all categories are in AUTO_POST_CATEGORIES (or you're confident enough), remove PAUSE_MODE entirely. The bot posts everything it categorizes, with filters still active.

**Changes:**
```python
PAUSE_MODE = False
AUTO_POST_CATEGORIES = []  # Empty = all categories auto-post (when PAUSE_MODE is False)
```

**Safety nets still active:**
- `NEWSWORTHINESS_FILTER_ENABLED = True` (threshold 6.0) — mundane entries still go to ignore
- `DUPLICATE_THRESHOLD = 0.95` — exact duplicates still suppressed
- `SIMILARITY_THRESHOLD = 0.75` + LLM verification — similar stories still suppressed
- `SHORT_VIDEO_FILTER_ENABLED = True` — short videos still go to ignore
- `AUDIENCE_QUESTION_FILTER_ENABLED = True` — engagement bait still goes to ignore
- `CAPS_FIX_ENABLED = True` — ALL CAPS still gets rewritten
- Feedback learning still running — AI keeps improving from your corrections

**Risk: Medium.** The bot posts autonomously, but the filter stack catches most garbage. You can still re-categorize or delete bad posts manually.

---

### Phase 4 — Enable Supersede (Medium Risk)

**What:** Turn on `SUPERSEDE_ENABLED`. When a better version of a story arrives, the bot replaces the old Discord message.

**Why:** This is genuinely useful automation — Dexerto posts a bare headline, then a follow-up tweet with the article URL. Supersede replaces the bare headline with the full article. Sources like Unusual Whales often post a quick alert then a detailed breakdown.

**Changes:**
```python
SUPERSEDE_ENABLED = True
```

**Safety:** The "Restore Original" context menu command already exists. If the bot supersedes incorrectly, you can right-click → Restore Original to undo it.

**Risk: Medium.** The supersede logic has an LLM comparison step (`compare_entries`) that checks whether the new entry is a strict upgrade. It's conservative — defaults to `keep_both` when uncertain. But it can still make mistakes. The undo mechanism mitigates this.

---

### Phase 5 — Enable Transcription (Optional, Low-Medium Risk)

**What:** Turn on `TRANSCRIPTION_ENABLED`. Video posts get their audio transcribed via Whisper (local, no API calls), and the transcript is used for better categorization and duplicate detection.

**Why:** Some Telegram channels (like drops_analytics) post video content where the spoken audio contains the actual news. Without transcription, the bot only sees the caption text (if any).

**Changes:**
```python
TRANSCRIPTION_ENABLED = True
```

**Risk: Low-Medium.** Whisper runs locally via faster-whisper. It adds processing time per video entry and ~3 GB RAM/VRAM usage. The `TRANSCRIPTION_MAX_DURATION = 300` cap prevents it from hogging resources on long videos.

---

## Additional Improvements (Not Phase-Dependent)

### A. Per-Category Newsworthiness Thresholds

Different categories have different "baseline" newsworthiness. A crypto price movement of 2% might be noise, but a 2% move in a specific stock after earnings is news. Allow per-category overrides:

```python
NEWSWORTHINESS_THRESHOLD = 6.0  # Default
NEWSWORTHINESS_THRESHOLD_BY_CATEGORY = {
    "crypto": 5.5,   # Lower bar — crypto moves fast, modest moves are still news
    "stocks": 5.5,
    "politics": 6.5,  # Higher bar — more political noise to filter
}
```

### B. Rate Limiting for Auto-Post

When auto-posting, prevent flooding a channel if a source dumps 50 entries at once:

```python
MAX_POSTS_PER_CATEGORY_PER_HOUR = 20  # Cap per category
MAX_POSTS_PER_SOURCE_PER_HOUR = 10    # Cap per source feed
```

Entries exceeding the cap go to ignore with a "rate limited" reason.

### C. Confidence Score from Categorizer

The Ollama categorizer could return a confidence score alongside the category. Low-confidence categorizations go to ignore even in auto-post mode:

```python
MIN_CATEGORY_CONFIDENCE = 0.7  # Only auto-post if AI is 70%+ confident
```

This would require a prompt change to ask the model to self-rate its confidence, which is imperfect but directionally useful.

### D. Dry-Run Mode

A mode where the bot processes everything normally but logs what it WOULD post instead of actually posting. Useful for testing config changes:

```python
DRY_RUN_MODE = False  # When True, log decisions but don't post to Discord
```

---

## Recommended Order of Operations

1. **Today:** Enable Phase 1 (feedback learning toggles). Zero risk, immediate benefit.
2. **1-2 weeks:** Let the AI learn from your corrections. Review the ignore channel to see if categorization quality improves.
3. **After 1-2 weeks:** Pick your most trusted category (maybe "crypto" or "stocks"). Add it to AUTO_POST_CATEGORIES, set PAUSE_MODE = False, and watch for a day.
4. **Graduate categories one at a time** over the following weeks.
5. **When all categories are graduated:** PAUSE_MODE = False, AUTO_POST_CATEGORIES = [] (empty = all).
6. **Then:** Enable SUPERSEDE (Phase 4).
7. **Optionally:** Enable transcription (Phase 5).
8. **Tune:** Adjust newsworthiness thresholds and add rate limiting if needed.

---

## What NOT to Change

- **DEFAULT_CATEGORY = "ignore"** — keep this. It's the safety net for unparseable AI responses.
- **FALLBACK_CATEGORY = "general news"** — keep this. Used when un-ignoring entries.
- **All filter toggles** (NEWSWORTHINESS, SHORT_VIDEO, AUDIENCE_QUESTION, CAPS_FIX) — keep them ON. They're your safety nets.
- **RECATEGORIZE_COMMAND_ENABLED, SOURCE_COMMAND_ENABLED, EDIT_TEXT_COMMAND_ENABLED, DELETE_COMMAND_ENABLED** — keep them ON. They're your manual override tools.