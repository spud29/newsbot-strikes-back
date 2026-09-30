"""Build the categorization golden set from the live SQLite DB.

Ground truth = the `category` column of `message_mapping` as it stands today
(post-any-human-re-categorization). Two confidence tiers:

- explicit: `placement_reason` starts with "User re-categorization: moved from",
  i.e. a human actively moved the entry. Rock-solid label.
- implicit: the AI's original choice was never changed
  (`original_category = category` and no user re-cat in placement_reason).

Includes `ignore`-channel entries in both tiers — negative examples are as
important as positive ones.

Sampling: stratified to ~TARGET_SIZE, per-category cap = max(15, proportional),
explicit prioritized over implicit within each stratum.

Fails loudly if the usable pool is < MIN_USABLE (200) — better to know the
golden set is thin than tune on noise.
"""
import argparse
import json
import os
import random
import sys
import time
from collections import defaultdict

from _paths import GOLDEN_PATH, ensure_project_on_syspath

ensure_project_on_syspath()

from db_connection import get_db_connection  # noqa: E402
import config  # noqa: E402

TARGET_SIZE = 500
MIN_USABLE = 200
WINDOW_DAYS = 90
MIN_CATEGORY_CAP = 15
EXPLICIT_PREFIX = "User re-categorization: moved from"


def fetch_rows(window_days: int):
    conn = get_db_connection()
    cutoff = time.time() - window_days * 86400
    rows = conn.execute(
        """
        SELECT entry_id, content, category, original_category, source_type,
               timestamp, placement_reason
          FROM message_mapping
         WHERE timestamp >= ?
           AND content IS NOT NULL
           AND TRIM(content) != ''
           AND category IS NOT NULL
        """,
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def label_confidence(row: dict) -> str | None:
    reason = row.get("placement_reason") or ""
    if reason.startswith(EXPLICIT_PREFIX):
        return "explicit"
    if row.get("original_category") and row["original_category"] == row["category"]:
        return "implicit"
    return None


def stratified_sample(labeled: list[dict], target: int) -> list[dict]:
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for row in labeled:
        by_cat[row["expected_category"]].append(row)

    total = sum(len(v) for v in by_cat.values())
    if total == 0:
        return []

    proportional = {cat: max(MIN_CATEGORY_CAP, round(target * len(v) / total))
                    for cat, v in by_cat.items()}

    selected: list[dict] = []
    rng = random.Random(42)
    for cat, rows in by_cat.items():
        cap = proportional[cat]
        explicit = [r for r in rows if r["label_confidence"] == "explicit"]
        implicit = [r for r in rows if r["label_confidence"] == "implicit"]
        rng.shuffle(explicit)
        rng.shuffle(implicit)
        picked = explicit[:cap]
        if len(picked) < cap:
            picked.extend(implicit[: cap - len(picked)])
        selected.extend(picked)

    rng.shuffle(selected)
    return selected[:target] if len(selected) > target else selected


def main():
    parser = argparse.ArgumentParser(description="Build categorization golden set from SQLite.")
    parser.add_argument("--out", default=GOLDEN_PATH)
    parser.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    parser.add_argument("--target", type=int, default=TARGET_SIZE)
    args = parser.parse_args()

    rows = fetch_rows(args.window_days)
    print(f"Fetched {len(rows)} rows in last {args.window_days} days", file=sys.stderr)

    labeled = []
    for row in rows:
        conf = label_confidence(row)
        if conf is None:
            continue
        labeled.append({
            "entry_id": row["entry_id"],
            "content": row["content"],
            "expected_category": row["category"],
            "source_type": row.get("source_type"),
            "label_confidence": conf,
            "timestamp": row["timestamp"],
        })

    print(f"Usable labels: {len(labeled)} "
          f"({sum(1 for r in labeled if r['label_confidence'] == 'explicit')} explicit, "
          f"{sum(1 for r in labeled if r['label_confidence'] == 'implicit')} implicit)",
          file=sys.stderr)

    if len(labeled) < MIN_USABLE:
        print(f"ERROR: only {len(labeled)} usable rows (need >= {MIN_USABLE}). "
              f"Lower --target or widen --window-days, or run the bot longer.", file=sys.stderr)
        sys.exit(2)

    sample = stratified_sample(labeled, args.target)

    per_cat = defaultdict(lambda: [0, 0])
    for row in sample:
        per_cat[row["expected_category"]][0 if row["label_confidence"] == "explicit" else 1] += 1

    print(f"\nFinal golden set: {len(sample)} examples", file=sys.stderr)
    print(f"{'category':<22} explicit  implicit", file=sys.stderr)
    for cat in sorted(per_cat.keys()):
        e, i = per_cat[cat]
        print(f"{cat:<22} {e:>8}  {i:>8}", file=sys.stderr)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for row in sample:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\nWrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
