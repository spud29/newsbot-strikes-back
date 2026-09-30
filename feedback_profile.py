"""
Feedback profile for NewsBot Monitor.

Tracks patterns from Brandi's manual promote/demote decisions in the Discord
message_mapping table, extracts feature patterns, and scores new entries against
those patterns during scans.

Two-sided:
- promote_patterns: what Brandi tends to promote → higher score = more likely to want
- demote_patterns: what Brandi tends to demote → higher score = more likely to reject
"""

import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone

FEEDBACK_PATH = os.path.expanduser("~/.hermes/cache/newsbot-feedback-profile.json")

# Canonical name map for the variant category names seen in message_mapping
# (grew via user re-categorization before the LLM settled on spaced names).
CATEGORY_VARIANT_MAP = {
    "world_news": "world news", "world-news": "world news",
    "us_politics": "us politics", "us-politics": "us politics", "uspolitics": "us politics",
    "artificial_intelligence": "artificial intelligence",
    "artificial-intelligence": "artificial intelligence",
    "general_news": "general news", "general-news": "general news", "general": "general news",
    "pop_culture": "pop culture", "pop-culture": "pop culture", "popculture": "pop culture",
    "science_and_technology": "science & technology",
    "science_tech": "science & technology",
    "science_technology": "science & technology",
    "science-&-technology": "science & technology",
    "video_games": "video games", "videoGames": "video games",
    "politics": "us politics",  # orphaned legacy category from the 2026-07-11 revamp
}


def canonical_category(name):
    """Map a variant category name to its canonical spaced form."""
    if not name:
        return name
    return CATEGORY_VARIANT_MAP.get(name, name)


def load_feedback():
    """Load the feedback profile from disk."""
    if os.path.exists(FEEDBACK_PATH):
        with open(FEEDBACK_PATH) as f:
            data = json.load(f)
            # Convert lists back to sets
            data["demoted_entry_ids"] = set(data.get("demoted_entry_ids", []))
            data["promoted_entry_ids"] = set(data.get("promoted_entry_ids", []))
            return data
    return {
        "created_at": None,
        "last_updated": None,
        "promote_patterns": {
            "content_features": {},
            "topic_keywords": {},
            "source_counts": {},
            "category_counts": {},
            "total_promotes": 0,
            "recent_promotes": [],
        },
        "demote_patterns": {
            "content_features": {},
            "topic_keywords": {},
            "source_counts": {},
            "category_counts": {},
            "total_demotes": 0,
            "recent_demotes": [],
        },
        "demoted_entry_ids": [],
        "promoted_entry_ids": [],
    }


def save_feedback(profile):
    """Save the feedback profile to disk."""
    profile["last_updated"] = datetime.now(timezone.utc).isoformat()
    to_save = {k: v for k, v in profile.items() if k not in ("demoted_entry_ids", "promoted_entry_ids")}
    if "demoted_entry_ids" in profile:
        to_save["demoted_entry_ids"] = list(profile["demoted_entry_ids"])
    if "promoted_entry_ids" in profile:
        to_save["promoted_entry_ids"] = list(profile["promoted_entry_ids"])
    os.makedirs(os.path.dirname(FEEDBACK_PATH), exist_ok=True)
    with open(FEEDBACK_PATH, "w") as f:
        json.dump(to_save, f, indent=2, default=str)


def _extract_content_features(content):
    """Extract boolean/text features from entry content."""
    features = {}

    features["has_polymarket_odds"] = bool(
        re.search(r"--\s*(chance|probability|odds|% chance|forecast)", content, re.IGNORECASE)
    )
    features["has_url"] = bool(re.search(r"https?://", content))

    dollar_matches = re.findall(r"\$\d[\d,.]*\s*(billion|million|trillion|B|M)", content)
    features["has_dollar_amounts"] = len(dollar_matches) > 0
    features["dollar_amount_count"] = len(dollar_matches)

    features["has_percentages"] = bool(re.search(r"\d+%", content))

    named_entities = re.findall(
        r"(?:Sen\.|Senator|Rep\.|Representative|Gov\.|Governor|President|CEO|Secretary|Minister|Dr\.|Prof\.)?\s([A-Z][a-z]+\s[A-Z][a-z]+)",
        content,
    )
    features["named_figures_count"] = len(named_entities)
    features["has_named_figures"] = len(named_entities) > 0

    dramatic_words = [
        "killed", "arrested", "pleaded", "guilty", "dies", "died", "replaced",
        "bans", "ban", "urges", "orders", "investigation", "scandal", "sued",
        "lawsuit", "probes", "probe", "charged", "resigns", "resigned",
        "collapses", "crashes", "surges", "plunges", "slams", "strikes",
        "destroys", "hacks", "breaches", "exploit", "exploited",
    ]
    features["has_dramatic_verb"] = any(w in content.lower() for w in dramatic_words)

    features["has_news_source_cite"] = bool(
        re.search(r"(?:per|according to)\s+(?:Bloomberg|Reuters|FT|WSJ|AP|NBC|ABC|CNN|Forbes|FORTUNE)", content)
    )

    features["content_length"] = len(content)
    features["is_short"] = len(content) < 150
    features["is_very_short"] = len(content) < 80

    features["is_newsletter"] = bool(
        re.search(r"(?:newsletter|weekly roundup|week in|biggest.*stories|stories of the week)", content, re.IGNORECASE)
    )
    features["is_listicle"] = bool(
        re.search(r"(?:biggest|top \d+|#1|#2|#3|list of|here are|following)", content, re.IGNORECASE)
    )

    local_keywords = [
        "Houston", "Texas", "Ceuta", "Warsaw", "Alaska", "Minnesota", "South Carolina",
        "Idaho", "Mumbai", "London", "Manchester", "Singapore", "Sri Lanka",
        "UC Irvine", "University of Iowa", "Manhattan", "Maryland",
    ]
    features["has_local_geo"] = any(geo in content for geo in local_keywords)

    features["has_crypto_keywords"] = bool(re.search(r"\$(?:BTC|ETH|SOL|AAVE|RAVE|WETH|USDC|MOON|PEPE|SHIB)", content))
    features["has_politics_keywords"] = bool(
        re.search(r"(?:Trump|Biden|Harris|Warren|Democratic|Republican|Congress|Senate|House|GOP|election|tariffs|ceasefire|peace deal|Iran|DOJ|SEC|Fed|federal|court|judge|supreme)", content, re.IGNORECASE)
    )
    features["has_stocks_keywords"] = bool(
        re.search(r"(?:stock|shares|market|S&P|Nasdaq|Dow|earnings|IPO|valuation|investors|portfolio|hedge fund|Berkshire)", content, re.IGNORECASE)
    )
    features["has_tech_keywords"] = bool(
        re.search(r"(?:Apple|Google|Microsoft|Nvidia|OpenAI|Anthropic|Claude|Gemini|AI|data center|chip|semiconductor|software|app|download|GitHub|Linux|Windows)", content, re.IGNORECASE)
    )

    features["has_separator_dash"] = content.count("--") >= 1

    return features


def _extract_topic_keywords(content):
    """Extract significant keywords from content for topic matching."""
    stopwords = {
        "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or", "but",
        "is", "are", "was", "were", "has", "have", "had", "be", "been", "being",
        "that", "this", "it", "its", "with", "from", "by", "as", "into", "through",
        "during", "before", "after", "above", "below", "between", "out", "off", "over",
        "under", "again", "further", "then", "once", "here", "there", "when", "where",
        "why", "how", "all", "each", "every", "both", "few", "more", "most", "other",
        "some", "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too",
        "very", "just", "because", "if", "while", "about", "up", "down", "said", "says",
        "reportedly", "according", "per", "via", "read", "more", "https", "http",
    }

    words = re.findall(r"[a-zA-Z]{4,}", content.lower())
    words = [w for w in words if w not in stopwords and not w.isdigit()]
    return words[:30]


def score_entry_against_profile(entry, profile):
    """Score a new entry against the feedback profile."""
    features = _extract_content_features(entry["content"])
    keywords = _extract_topic_keywords(entry["content"])

    promote_score = 0
    demote_score = 0
    details = []

    pp = profile.get("promote_patterns", {})
    dp = profile.get("demote_patterns", {})

    if pp.get("total_promotes", 0) == 0 and dp.get("total_demotes", 0) == 0:
        return {
            "promote_match_score": 0,
            "demote_match_score": 0,
            "match_details": ["No feedback data yet — profile is empty."],
        }

    # Polymarket odds → strong demote signal
    if features["has_polymarket_odds"]:
        demote_score += 25
        details.append("Has Polymarket odds appended — matches demote pattern")

    # Dramatic verbs → promote signal
    if features["has_dramatic_verb"]:
        promote_score += 15
        details.append("Has dramatic action verb — matches promote pattern")

    # Named figures → promote signal
    if features["has_named_figures"]:
        promote_score += 10
        details.append(f"Has {features['named_figures_count']} named figure(s) — matches promote pattern")
    else:
        demote_score += 5
        details.append("No named figures — weak promote signal")

    # News source citation → promote signal
    if features["has_news_source_cite"]:
        promote_score += 10
        details.append("Cites named news source (Bloomberg/Reuters/etc) — promote pattern")

    # Dollar amounts without narrative → demote signal
    if features["has_dollar_amounts"] and not features["has_dramatic_verb"] and not features["has_named_figures"]:
        demote_score += 20
        details.append("Dollar amounts without narrative/drama — matches demote pattern (pure finance)")

    # Newsletter/listicle → demote signal
    if features["is_newsletter"] or features["is_listicle"]:
        demote_score += 25
        details.append("Newsletter/listicle format — matches demote pattern")

    # Very short → demote signal
    if features["is_very_short"]:
        demote_score += 15
        details.append("Very short entry (<80 chars) — matches demote pattern (thin content)")
    elif features["is_short"]:
        demote_score += 5
        details.append("Short entry (<150 chars) — weak demote signal")

    # Local geo → weak demote signal
    if features["has_local_geo"]:
        demote_score += 10
        details.append("Local/geo-specific news — weak demote signal (often too narrow)")

    # Topic keyword overlap with promoted entries
    promoted_topics = set(pp.get("topic_keywords", {}).keys())
    matched_topics = [kw for kw in keywords if kw in promoted_topics]
    if matched_topics:
        promote_score += min(len(matched_topics) * 3, 15)
        details.append(f"Topic overlap with promoted entries: {', '.join(matched_topics[:5])}")

    # Topic keyword overlap with demoted entries
    demoted_topics = set(dp.get("topic_keywords", {}).keys())
    matched_demote_topics = [kw for kw in keywords if kw in demoted_topics]
    if matched_demote_topics:
        demote_score += min(len(matched_demote_topics) * 3, 15)
        details.append(f"Topic overlap with demoted entries: {', '.join(matched_demote_topics[:5])}")

    # Hard veto: previously demoted entry ID
    if entry["entry_id"] in profile.get("demoted_entry_ids", []):
        demote_score = 100
        details.append("EXACT MATCH: This entry was previously demoted by you — hard veto")

    # Previously promoted entry ID
    if entry["entry_id"] in profile.get("promoted_entry_ids", []):
        promote_score = 100
        details.append("EXACT MATCH: This entry was previously promoted by you")

    promote_score = min(promote_score, 100)
    demote_score = min(demote_score, 100)

    return {
        "promote_match_score": promote_score,
        "demote_match_score": demote_score,
        "match_details": details,
    }


def rebuild_profile_from_db(db_conn):
    """Rebuild the entire feedback profile from the message_mapping table."""
    profile = load_feedback()

    profile["promote_patterns"] = {
        "content_features": {},
        "topic_keywords": {},
        "source_counts": {},
        "category_counts": {},
        "total_promotes": 0,
        "recent_promotes": [],
    }
    profile["demote_patterns"] = {
        "content_features": {},
        "topic_keywords": {},
        "source_counts": {},
        "category_counts": {},
        "total_demotes": 0,
        "recent_demotes": [],
    }
    profile["demoted_entry_ids"] = set()
    profile["promoted_entry_ids"] = set()

    cursor = db_conn.cursor()

    # Entries where @big_brandi moved FROM ignore TO something (promotes)
    cursor.execute("""
        SELECT entry_id, category, placement_reason, content, timestamp, source_type
        FROM message_mapping
        WHERE placement_reason LIKE '%@big_brandi%'
          AND placement_reason LIKE '%moved from%ignore%'
          AND category != 'ignore'
        ORDER BY timestamp DESC
    """)
    promotes = cursor.fetchall()

    for row in promotes:
        entry_id = row[0]
        category = canonical_category(row[1])
        content = row[3]
        source_type = row[4] or "unknown"

        profile["promoted_entry_ids"].add(entry_id)
        profile["promote_patterns"]["total_promotes"] += 1
        profile["promote_patterns"]["category_counts"][category] = (
            profile["promote_patterns"]["category_counts"].get(category, 0) + 1
        )
        profile["promote_patterns"]["source_counts"][source_type] = (
            profile["promote_patterns"]["source_counts"].get(source_type, 0) + 1
        )

        features = _extract_content_features(content)
        for feat, val in features.items():
            if isinstance(val, bool) and val:
                profile["promote_patterns"]["content_features"][feat] = (
                    profile["promote_patterns"]["content_features"].get(feat, 0) + 1
                )

        keywords = _extract_topic_keywords(content)
        for kw in keywords:
            profile["promote_patterns"]["topic_keywords"][kw] = (
                profile["promote_patterns"]["topic_keywords"].get(kw, 0) + 1
            )

        profile["promote_patterns"]["recent_promotes"].append(content[:200])
        profile["promote_patterns"]["recent_promotes"] = profile["promote_patterns"]["recent_promotes"][-20:]

    # Entries where @big_brandi moved FROM something TO ignore (demotes)
    cursor.execute("""
        SELECT entry_id, category, placement_reason, content, timestamp, source_type
        FROM message_mapping
        WHERE placement_reason LIKE '%@big_brandi%'
          AND placement_reason LIKE '%moved from%'
          AND placement_reason LIKE '%ignore%'
          AND category = 'ignore'
        ORDER BY timestamp DESC
    """)
    demotes = cursor.fetchall()

    for row in demotes:
        entry_id = row[0]
        from_category = canonical_category(row[1])
        content = row[3]
        source_type = row[4] or "unknown"

        profile["demoted_entry_ids"].add(entry_id)
        profile["demote_patterns"]["total_demotes"] += 1
        profile["demote_patterns"]["category_counts"][from_category] = (
            profile["demote_patterns"]["category_counts"].get(from_category, 0) + 1
        )
        profile["demote_patterns"]["source_counts"][source_type] = (
            profile["demote_patterns"]["source_counts"].get(source_type, 0) + 1
        )

        features = _extract_content_features(content)
        for feat, val in features.items():
            if isinstance(val, bool) and val:
                profile["demote_patterns"]["content_features"][feat] = (
                    profile["demote_patterns"]["content_features"].get(feat, 0) + 1
                )

        keywords = _extract_topic_keywords(content)
        for kw in keywords:
            profile["demote_patterns"]["topic_keywords"][kw] = (
                profile["demote_patterns"]["topic_keywords"].get(kw, 0) + 1
            )

        profile["demote_patterns"]["recent_demotes"].append(content[:200])
        profile["demote_patterns"]["recent_demotes"] = profile["demote_patterns"]["recent_demotes"][-20:]

    if profile["created_at"] is None:
        profile["created_at"] = datetime.now(timezone.utc).isoformat()

    save_feedback(profile)
    return profile


def update_profile_from_new_action(db_conn, entry_id, action, from_category, to_category, content):
    """Update the feedback profile when a new user action is detected."""
    profile = load_feedback()
    source_type = "unknown"

    # Try to get source_type from DB
    try:
        cursor = db_conn.cursor()
        cursor.execute("SELECT source_type FROM message_mapping WHERE entry_id = ?", (entry_id,))
        row = cursor.fetchone()
        if row and row[0]:
            source_type = row[0]
    except Exception:
        pass

    if action == "promote":
        to_category = canonical_category(to_category)
        if entry_id not in profile["promoted_entry_ids"]:
            profile["promoted_entry_ids"].add(entry_id)
            profile["promote_patterns"]["total_promotes"] += 1
            profile["promote_patterns"]["category_counts"][to_category] = (
                profile["promote_patterns"]["category_counts"].get(to_category, 0) + 1
            )
            profile["promote_patterns"]["source_counts"][source_type] = (
                profile["promote_patterns"]["source_counts"].get(source_type, 0) + 1
            )

            features = _extract_content_features(content)
            for feat, val in features.items():
                if isinstance(val, bool) and val:
                    profile["promote_patterns"]["content_features"][feat] = (
                        profile["promote_patterns"]["content_features"].get(feat, 0) + 1
                    )

            keywords = _extract_topic_keywords(content)
            for kw in keywords:
                profile["promote_patterns"]["topic_keywords"][kw] = (
                    profile["promote_patterns"]["topic_keywords"].get(kw, 0) + 1
                )

            profile["promote_patterns"]["recent_promotes"].append(content[:200])
            profile["promote_patterns"]["recent_promotes"] = profile["promote_patterns"]["recent_promotes"][-20:]

    elif action == "demote":
        from_category = canonical_category(from_category)
        if entry_id not in profile["demoted_entry_ids"]:
            profile["demoted_entry_ids"].add(entry_id)
            profile["demote_patterns"]["total_demotes"] += 1
            profile["demote_patterns"]["category_counts"][from_category] = (
                profile["demote_patterns"]["category_counts"].get(from_category, 0) + 1
            )
            profile["demote_patterns"]["source_counts"][source_type] = (
                profile["demote_patterns"]["source_counts"].get(source_type, 0) + 1
            )

            features = _extract_content_features(content)
            for feat, val in features.items():
                if isinstance(val, bool) and val:
                    profile["demote_patterns"]["content_features"][feat] = (
                        profile["demote_patterns"]["content_features"].get(feat, 0) + 1
                    )

            keywords = _extract_topic_keywords(content)
            for kw in keywords:
                profile["demote_patterns"]["topic_keywords"][kw] = (
                    profile["demote_patterns"]["topic_keywords"].get(kw, 0) + 1
                )

            profile["demote_patterns"]["recent_demotes"].append(content[:200])
            profile["demote_patterns"]["recent_demotes"] = profile["demote_patterns"]["recent_demotes"][-20:]

    save_feedback(profile)
    return profile


def print_profile_summary(profile):
    """Print a human-readable summary of the feedback profile."""
    pp = profile.get("promote_patterns", {})
    dp = profile.get("demote_patterns", {})

    print(f"\n{'=' * 60}")
    print("FEEDBACK PROFILE SUMMARY")
    print(f"{'=' * 60}")
    print(f"Created: {profile.get('created_at', 'never')}")
    print(f"Last updated: {profile.get('last_updated', 'never')}")
    print()

    print(f"--- PROMOTES ({pp.get('total_promotes', 0)} total) ---")
    if pp.get("category_counts"):
        print(f"Categories promoted: {dict(sorted(pp['category_counts'].items(), key=lambda x: -x[1])[:8])}")
    if pp.get("source_counts"):
        print(f"Sources: {dict(sorted(pp['source_counts'].items(), key=lambda x: -x[1])[:5])}")
    if pp.get("content_features"):
        print(f"Top content features: {dict(sorted(pp['content_features'].items(), key=lambda x: -x[1])[:8])}")
    if pp.get("topic_keywords"):
        print(f"Top topic keywords: {dict(sorted(pp['topic_keywords'].items(), key=lambda x: -x[1])[:15])}")
    print()

    print(f"--- DEMOTES ({dp.get('total_demotes', 0)} total) ---")
    if dp.get("category_counts"):
        print(f"Categories demoted from: {dict(sorted(dp['category_counts'].items(), key=lambda x: -x[1])[:8])}")
    if dp.get("source_counts"):
        print(f"Sources: {dict(sorted(dp['source_counts'].items(), key=lambda x: -x[1])[:5])}")
    if dp.get("content_features"):
        print(f"Top content features: {dict(sorted(dp['content_features'].items(), key=lambda x: -x[1])[:8])}")
    if dp.get("topic_keywords"):
        print(f"Top topic keywords: {dict(sorted(dp['topic_keywords'].items(), key=lambda x: -x[1])[:15])}")
    print()

    print(f"--- RECENT PROMOTES ---")
    for i, snippet in enumerate(pp.get("recent_promotes", [])[-5:], 1):
        print(f"  {i}. {snippet[:120]}...")
    print()

    print(f"--- RECENT DEMOTES ---")
    for i, snippet in enumerate(dp.get("recent_demotes", [])[-5:], 1):
        print(f"  {i}. {snippet[:120]}...")
    print()

    print(f"--- PATTERN INSIGHTS ---")
    promote_feats = pp.get("content_features", {})
    demote_feats = dp.get("content_features", {})

    print("Brandi tends to PROMOTE entries that are:")
    for feat in ["has_dramatic_verb", "has_named_figures", "has_news_source_cite",
                 "has_politics_keywords", "has_crypto_keywords", "has_stocks_keywords"]:
        p_count = promote_feats.get(feat, 0)
        d_count = demote_feats.get(feat, 0)
        if p_count > 0 or d_count > 0:
            print(f"  - {feat.replace('_', ' ')}: {p_count} promotes vs {d_count} demotes")

    print("Brandi tends to DEMOTE entries that are:")
    for feat in ["has_polymarket_odds", "is_newsletter", "is_listicle",
                 "is_very_short", "has_dollar_amounts", "has_local_geo", "has_separator_dash"]:
        p_count = promote_feats.get(feat, 0)
        d_count = demote_feats.get(feat, 0)
        if d_count > 0 or p_count > 0:
            print(f"  - {feat.replace('_', ' ')}: {p_count} promotes vs {d_count} demotes")

    print()
