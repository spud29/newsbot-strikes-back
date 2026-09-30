"""Calibrate the reaction-worthiness (newsworthiness) gate against user decisions.

Under pause mode the user manually sorted every entry: moving one from the
ignore channel to the unified channel is a "this deserved to post" label, and
leaving it in ignore is an implicit "boring" label. This script replays those
labeled entries through the live rate_newsworthiness rater and reports how well
each candidate threshold separates the two populations, for two decisions:

  GATE   (AI assigned a real category): should this post or go to ignore?
           posted_pos  = user moved it to a real category  -> gate should PASS
           posted_neg  = user left it in ignore (>min-age) -> gate should FAIL
  RESCUE (AI said ignore): is this worth rescuing anyway?
           rescue_pos  = user promoted it out of ignore    -> rescue should catch
           junk        = user left it in ignore (>min-age) -> rescue must NOT catch

Read-only against the DB. Ollama must be running. Results are written to
evals/runs/reaction_calibration_<ts>.json so thresholds can be re-derived
without re-running the model.

Usage:
    python evals/calibrate_reaction_gate.py --counts       # label pool sizes only
    python evals/calibrate_reaction_gate.py                # full run (4 x 120 samples)
    python evals/calibrate_reaction_gate.py --per-class 60 # faster smoke run
"""
import argparse
import json
import os
import random
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

from _paths import RUNS_DIR, PROJECT_ROOT, ensure_project_on_syspath

ensure_project_on_syspath()

DB_PATH = os.path.join(PROJECT_ROOT, "data", "newsbot.db")

USER_MOVE_PREFIXES = ("User re-categorization%", "User label update%")

LABEL_QUERIES = {
    # User actively moved it out of ignore and the AI had assigned a real category.
    "posted_pos": """
        SELECT entry_id, content, original_category, timestamp FROM message_mapping
        WHERE timestamp >= :cutoff
          AND (placement_reason LIKE 'User re-categorization%'
               OR placement_reason LIKE 'User label update%')
          AND category != 'ignore'
          AND COALESCE(original_category, '') NOT IN ('', 'ignore')
          AND content IS NOT NULL AND TRIM(content) != ''
    """,
    # Pause mode sent it to ignore with a real AI category; user never moved it.
    "posted_neg": """
        SELECT entry_id, content, original_category, timestamp FROM message_mapping
        WHERE timestamp >= :cutoff AND timestamp < :max_ts
          AND placement_reason LIKE 'Pause mode override%'
          AND category = 'ignore'
          AND COALESCE(original_category, 'ignore') != 'ignore'
          AND content IS NOT NULL AND TRIM(content) != ''
    """,
    # AI said ignore, user promoted it to a real category.
    "rescue_pos": """
        SELECT entry_id, content, original_category, timestamp FROM message_mapping
        WHERE timestamp >= :cutoff
          AND original_category = 'ignore' AND category != 'ignore'
          AND content IS NOT NULL AND TRIM(content) != ''
    """,
    # AI said ignore and the user agreed (left it alone past the review window).
    "junk": """
        SELECT entry_id, content, original_category, timestamp FROM message_mapping
        WHERE timestamp >= :cutoff AND timestamp < :max_ts
          AND original_category = 'ignore' AND category = 'ignore'
          AND content IS NOT NULL AND TRIM(content) != ''
    """,
}


def fetch_pools(window_days: int, min_age_hours: float) -> dict:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        params = {
            "cutoff": time.time() - window_days * 86400,
            "max_ts": time.time() - min_age_hours * 3600,
        }
        return {
            label: [dict(r) for r in conn.execute(sql, params)]
            for label, sql in LABEL_QUERIES.items()
        }
    finally:
        conn.close()


def sweep(pos_scores: list, neg_scores: list, lo=3.0, hi=9.0, step=0.25) -> list:
    """Return per-threshold precision/recall/F1 for pass-if-score>=t."""
    rows = []
    t = lo
    while t <= hi + 1e-9:
        tp = sum(1 for s in pos_scores if s >= t)
        fp = sum(1 for s in neg_scores if s >= t)
        fn = len(pos_scores) - tp
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        rows.append({"threshold": round(t, 2), "precision": precision,
                     "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn,
                     "neg_passed_pct": fp / len(neg_scores) if neg_scores else 0.0})
        t += step
    return rows


def print_histogram(label: str, scores: list):
    buckets = defaultdict(int)
    for s in scores:
        buckets[int(min(9, max(1, s)))] += 1
    total = len(scores)
    mean = sum(scores) / total if total else 0
    print(f"\n{label} (n={total}, mean={mean:.2f})")
    for b in range(1, 10):
        n = buckets[b]
        bar = "#" * round(40 * n / total) if total else ""
        print(f"  {b}-{b + 1}: {n:>4} {bar}")


def print_sweep(title: str, rows: list):
    print(f"\n{title}")
    print(f"{'THRESH':>7} {'PREC':>6} {'RECALL':>7} {'F1':>6} {'%NEG PASSED':>12}")
    for r in rows:
        print(f"{r['threshold']:>7.2f} {r['precision']:>6.1%} {r['recall']:>7.1%} "
              f"{r['f1']:>6.3f} {r['neg_passed_pct']:>12.1%}")


def main():
    parser = argparse.ArgumentParser(description="Calibrate the reaction-worthiness gate.")
    parser.add_argument("--counts", action="store_true", help="Print label pool sizes and exit.")
    parser.add_argument("--per-class", type=int, default=120)
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--min-age-hours", type=float, default=24.0,
                        help="Implicit negatives must be older than this (review lag guard).")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    pools = fetch_pools(args.window_days, args.min_age_hours)
    print("Label pools:", {k: len(v) for k, v in pools.items()}, file=sys.stderr)
    if args.counts:
        return

    rng = random.Random(args.seed)
    samples = {}
    for label, rows in pools.items():
        rng.shuffle(rows)
        samples[label] = rows[: args.per_class]

    from ollama_client import OllamaClient
    ollama = OllamaClient()  # bare client: no feedback layers, just the rater

    scored = {}
    total = sum(len(v) for v in samples.values())
    done = 0
    started = time.time()
    for label, rows in samples.items():
        scored[label] = []
        for row in rows:
            # Rate with the AI's own category, exactly as the live pipeline does.
            # For AI-ignored entries there is no real category; rate as 'general news'.
            cat = row["original_category"] if row["original_category"] != "ignore" else "general news"
            result = ollama.rate_newsworthiness(row["content"], cat)
            scored[label].append({
                "entry_id": row["entry_id"],
                "content": row["content"][:300],
                "category": cat,
                "score": result["score"],
                "reasoning": result["reasoning"],
            })
            done += 1
            if done % 10 == 0:
                rate = done / (time.time() - started)
                eta = (total - done) / rate if rate else 0
                print(f"  {done}/{total} scored ({rate:.1f}/s, eta {eta / 60:.0f}m)", file=sys.stderr)

    gate_sweep = sweep([r["score"] for r in scored["posted_pos"]],
                       [r["score"] for r in scored["posted_neg"]])
    rescue_sweep = sweep([r["score"] for r in scored["rescue_pos"]],
                         [r["score"] for r in scored["junk"]])

    for label in ("posted_pos", "posted_neg", "rescue_pos", "junk"):
        print_histogram(label, [r["score"] for r in scored[label]])

    print_sweep("GATE SWEEP (posted_pos vs posted_neg) — pass if score >= t", gate_sweep)
    print_sweep("RESCUE SWEEP (rescue_pos vs junk) — rescue if score >= t", rescue_sweep)

    best_gate = max(gate_sweep, key=lambda r: r["f1"])
    print(f"\nBest gate threshold by F1: {best_gate['threshold']} "
          f"(P {best_gate['precision']:.1%} / R {best_gate['recall']:.1%})")
    # Rescue must be high-precision: junk flooding back defeats the point.
    safe_rescues = [r for r in rescue_sweep if r["neg_passed_pct"] <= 0.05]
    if safe_rescues:
        best_rescue = max(safe_rescues, key=lambda r: r["recall"])
        print(f"Best rescue threshold (<=5% junk passed): {best_rescue['threshold']} "
              f"(catches {best_rescue['recall']:.1%} of rescues)")
    else:
        print("No rescue threshold keeps junk pass-through <= 5% — leave rescue disabled.")

    ts_slug = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = os.path.join(RUNS_DIR, f"reaction_calibration_{ts_slug}.json")
    os.makedirs(RUNS_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "per_class": args.per_class,
            "window_days": args.window_days,
            "min_age_hours": args.min_age_hours,
            "scored": scored,
            "gate_sweep": gate_sweep,
            "rescue_sweep": rescue_sweep,
        }, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
