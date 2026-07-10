"""Categorization accuracy report.

Measures how well the AI categorizer agrees with the user's judgment, using the
correction data already recorded in message_mapping. Used to decide whether a
category is ready to graduate to AUTO_POST_CATEGORIES (see PLAN.md).

The bot posts this report to Discord automatically on a schedule
(ACCURACY_REPORT_* settings in config.py); it can also be run manually.

Read-only: opens the database with mode=ro and never writes.

Usage:
    python accuracy_report.py               # last 7 days
    python accuracy_report.py --days 14     # last 14 days
    python accuracy_report.py --days 0      # all time
"""

import argparse
import sqlite3
import time
from collections import Counter, defaultdict

DB_PATH = "data/newsbot.db"

# Parsed from placement_reason prefixes. Insert-time prefixes are written in
# main.py process_entry(); the "user_moved" prefixes overwrite them when the
# user re-categorizes an entry, so user action takes precedence here.
ROUTING_CAUSES = [
    ("Pause mode override", "paused"),
    ("Duplicate override", "duplicate"),
    ("Content filter: newsworthiness", "newsworthiness"),
    ("Content filter: short video", "short_video"),
    ("Content filter: audience engagement", "audience_question"),
    ("AI categorized as", "direct"),
    ("User re-categorization", "user_moved"),
    ("User label update", "user_moved"),
]

# Graduation suggestion thresholds
MIN_REVIEWED = 20      # need at least this many user-reviewed entries
MIN_AGREEMENT = 0.95   # and this much agreement among them


def routing_cause(placement_reason):
    for prefix, cause in ROUTING_CAUSES:
        if placement_reason and placement_reason.startswith(prefix):
            return cause
    return "other"


def classify(row):
    """Classify one entry by what the AI said vs. where it ended up.

    Returns (ai_category, outcome) where outcome is one of:
      rescued        - AI said ignore, user promoted to a real category
      stayed_ignored - AI said ignore, still in ignore (presumed correct)
      confirmed      - user promoted out of ignore into the AI's category
      corrected      - user placed it in a different real category than the AI chose
      demoted        - AI posted it (or would have), user moved it to ignore
      in_review      - sitting in ignore awaiting review (paused/filtered, untouched)
      untouched      - posted directly with AI's category, user never intervened
    """
    ai_cat = row["original_category"] or row["category"]
    cur_cat = row["category"]
    cause = routing_cause(row["placement_reason"])

    if ai_cat == "ignore":
        return ai_cat, ("rescued" if cur_cat != "ignore" else "stayed_ignored")

    if cause == "user_moved":
        if cur_cat == "ignore":
            return ai_cat, "demoted"
        return ai_cat, ("confirmed" if cur_cat == ai_cat else "corrected")

    if cause == "direct":
        if cur_cat == "ignore":
            return ai_cat, "demoted"
        return ai_cat, ("untouched" if cur_cat == ai_cat else "corrected")

    # Routed to ignore by pause mode or a filter; only a user move changes category
    if cur_cat == "ignore":
        return ai_cat, "in_review"
    return ai_cat, ("confirmed" if cur_cat == ai_cat else "corrected")


def build_report(days=7, db_path=DB_PATH):
    """Build the accuracy report and return it as a plain-text string.

    Args:
        days: look-back window in days (0 = all time)
        db_path: path to the newsbot SQLite database (opened read-only)
    """
    out = []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cutoff = 0 if days == 0 else time.time() - days * 86400
        window_label = "all time" if days == 0 else f"last {days} days"

        rows = conn.execute(
            """SELECT category, original_category, placement_reason, user_reason
               FROM message_mapping
               WHERE timestamp >= ?""",
            (cutoff,),
        ).fetchall()

        out.append("=" * 80)
        out.append(f"CATEGORIZATION ACCURACY REPORT ({window_label}, {len(rows)} entries)")
        out.append("=" * 80)

        if not rows:
            out.append("No entries in this window.")
            return "\n".join(out)

        # ---- Placement overview (user moves overwrite the insert-time reason) ----
        cause_counts = Counter(routing_cause(r["placement_reason"]) for r in rows)
        out.append("\nPLACEMENT BREAKDOWN")
        out.append("-" * 40)
        for cause, count in cause_counts.most_common():
            out.append(f"{cause:20} {count:6}")

        # ---- Per-category outcomes ----
        stats = defaultdict(Counter)
        confusion = Counter()
        rescues = Counter()
        for row in rows:
            ai_cat, outcome = classify(row)
            stats[ai_cat][outcome] += 1
            if outcome == "corrected":
                confusion[(ai_cat, row["category"])] += 1
            elif outcome == "rescued":
                rescues[row["category"]] += 1

        out.append("\nPER-CATEGORY AGREEMENT (AI verdict vs. user judgment)")
        out.append("-" * 80)
        out.append(f"{'AI CATEGORY':25} {'TOTAL':>6} {'AGREE':>6} {'WRONG':>6} {'PENDING':>8} {'AGREEMENT':>10}")
        out.append("-" * 80)

        graduation_candidates = []
        for ai_cat in sorted(stats, key=lambda c: -sum(stats[c].values())):
            s = stats[ai_cat]
            total = sum(s.values())
            if ai_cat == "ignore":
                agree = s["stayed_ignored"]
                wrong = s["rescued"]
                pending = 0
            else:
                agree = s["confirmed"] + s["untouched"]
                wrong = s["corrected"] + s["demoted"]
                pending = s["in_review"]
            reviewed = agree + wrong
            agreement = agree / reviewed if reviewed else None
            agreement_str = f"{agreement:>9.1%}" if agreement is not None else "       n/a"
            out.append(f"{ai_cat:25} {total:>6} {agree:>6} {wrong:>6} {pending:>8} {agreement_str:>10}")
            if (ai_cat != "ignore" and reviewed >= MIN_REVIEWED
                    and agreement is not None and agreement >= MIN_AGREEMENT):
                graduation_candidates.append((ai_cat, reviewed, agreement))

        out.append("-" * 80)
        out.append("AGREE = user confirmed AI's category (or never intervened on a direct post)")
        out.append("WRONG = user re-categorized, demoted to ignore, or rescued from ignore")
        out.append("PENDING = still in ignore channel (paused/filtered), not yet reviewed")

        # ---- Confusion pairs ----
        if confusion:
            out.append("\nCONFUSION PAIRS (AI said X, user corrected to Y)")
            out.append("-" * 60)
            out.append(f"{'AI SAID':25} {'USER CORRECTED TO':25} {'COUNT':>6}")
            for (frm, to), count in confusion.most_common(20):
                out.append(f"{frm:25} {to:25} {count:>6}")

        # ---- Ignore rescues ----
        if rescues:
            out.append("\nIGNORE RESCUES (AI said ignore, user promoted)")
            out.append("-" * 40)
            for cat, count in rescues.most_common():
                out.append(f"{cat:25} {count:>6}")

        # ---- Removals ----
        removed = conn.execute(
            """SELECT category, COUNT(*) AS count FROM removed_entries
               WHERE removed_at >= ? GROUP BY category ORDER BY count DESC""",
            (cutoff,),
        ).fetchall()
        if removed:
            out.append("\nREMOVED ENTRIES (user deleted after posting)")
            out.append("-" * 40)
            for row in removed:
                out.append(f"{str(row['category']):25} {row['count']:>6}")

        # ---- Graduation suggestions ----
        out.append("\nGRADUATION CANDIDATES")
        out.append("-" * 80)
        if graduation_candidates:
            for cat, reviewed, agreement in sorted(graduation_candidates, key=lambda x: -x[2]):
                out.append(f"  {cat}: {agreement:.1%} agreement over {reviewed} reviewed entries "
                           f"-> candidate for AUTO_POST_CATEGORIES")
        else:
            out.append(f"  None yet (need >= {MIN_REVIEWED} reviewed entries at >= {MIN_AGREEMENT:.0%} "
                       f"agreement in this window).")
            out.append("  Keep reviewing the ignore channel — every move/re-categorization is training data.")

        return "\n".join(out)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Categorization accuracy report")
    parser.add_argument("--days", type=int, default=7,
                        help="Look-back window in days (0 = all time, default 7)")
    parser.add_argument("--db", default=DB_PATH, help=f"Database path (default {DB_PATH})")
    args = parser.parse_args()
    print(build_report(days=args.days, db_path=args.db))


if __name__ == "__main__":
    main()
