"""Compare OpenRouter categorization against qwen3.5's real production decisions.

Why not run_eval.py: evals/categorization_golden.jsonl was built 2026-07-09, before
the 2026-07-11 category revamp. 162 of its 500 rows (32%) carry labels that no longer
exist — 'politics' (159, since split into 'us politics'/'world news'), plus 'fashion',
'music' and 'software development'. Scored against that file, a correct 'us politics'
answer counts as wrong, so its macro-F1 says nothing about backend quality until the
golden set is relabelled.

Why not run both backends live: local qwen3.5 inference competes with the running bot
for a GPU that is already at 100%, which made a 20-row head-to-head take >10 minutes.

Instead this uses message_mapping, where every entry the bot has processed since the
revamp already carries qwen3.5's own verdict:
    original_category    - what qwen3.5 said (the baseline, free)
    category             - the final label; where user_edited=1 this is the USER'S
                           correction, i.e. real ground truth
    newsworthiness_score - qwen3.5's gate score

So we run only OpenRouter and compare against what local inference already decided on
the exact same content, at natural production distribution.

Usage:
    python evals/compare_backends.py --n 150
    python evals/compare_backends.py --n 150 --rate    # also compare gate scores
"""
import argparse
import collections
import datetime
import json
import os
import random
import sqlite3
import sys
import time

from _paths import RUNS_DIR, ensure_project_on_syspath

ensure_project_on_syspath()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "newsbot.db")
# config.DB_PATH is relative ("data/newsbot.db"), so Database() resolves against cwd.
# Run from evals/ and it silently creates an EMPTY evals/data/newsbot.db, which strips
# every feedback-learning example out of the system prompt — the eval then measures a
# ~4.3k-token prompt while production sends ~8.5k. Pin cwd so that cannot happen.
os.chdir(PROJECT_ROOT)
# Labels recorded before the 2026-07-11 revamp use the retired taxonomy.
REVAMP_CUTOFF = datetime.datetime(2026, 7, 12, tzinfo=datetime.timezone.utc).timestamp()


def fetch_rows(n, seed, user_edited_only=False):
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # user_edited rows are the only true ground truth here: the user overrode the
    # AI, so `category` is a human label. There are few of them, so they are worth
    # running as their own pass rather than waiting for them to turn up in a sample.
    extra = "AND user_edited = 1 AND category != original_category" if user_edited_only else ""
    rows = [dict(r) for r in conn.execute(
        f"""SELECT entry_id, content, category, original_category, user_edited,
                   newsworthiness_score
            FROM message_mapping
            WHERE timestamp >= ?
              AND content IS NOT NULL AND content != ''
              AND original_category IS NOT NULL
              {extra}""",
        (REVAMP_CUTOFF,),
    )]
    conn.close()
    # Natural distribution, not stratified: the headline question is what OpenRouter
    # would do to the real feed, so rare categories should stay rare.
    random.Random(seed).shuffle(rows)
    return rows[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--rate", action="store_true",
                    help="Also compare rate_newsworthiness against stored scores.")
    ap.add_argument("--user-edited-only", action="store_true",
                    help="Only entries the user re-categorized (real ground truth).")
    args = ap.parse_args()

    os.environ["LLM_BACKEND"] = "openrouter"
    import config
    from ollama_client import OllamaClient
    from database import Database
    assert config.LLM_BACKEND == "openrouter", "backend override failed"

    rows = fetch_rows(args.n, args.seed, args.user_edited_only)
    print(f"Scoring {len(rows)} post-revamp production entries via "
          f"{config.OPENROUTER_CATEGORIZATION_MODEL}\n", file=sys.stderr)

    client = OllamaClient(database=Database())
    results = []
    started = time.time()
    for i, r in enumerate(rows, 1):
        cat, reasoning, _sec = client.categorize(r["content"])
        rec = {**r, "or_category": cat, "or_reasoning": reasoning}
        if args.rate:
            rate_cat = cat if cat != "ignore" else "general news"
            rec["or_score"] = client.rate_newsworthiness(r["content"], rate_cat)["score"]
        results.append(rec)
        if i % 25 == 0 or i == len(rows):
            print(f"  {i}/{len(rows)}  {i/(time.time()-started):.2f} ex/s", file=sys.stderr)

    n = len(results)
    agree = sum(r["or_category"] == r["original_category"] for r in results)

    q_ign = sum(r["original_category"] == "ignore" for r in results)
    o_ign = sum(r["or_category"] == "ignore" for r in results)

    print("\n" + "=" * 74)
    print(f"AGREEMENT WITH qwen3.5 PRODUCTION DECISIONS:  {agree}/{n} = {agree/n:.1%}")
    print("=" * 74)

    print("\n'ignore' rate — decides whether the feed floods or goes quiet:")
    print(f"  qwen3.5 (production)  {q_ign}/{n} = {q_ign/n:.1%}")
    print(f"  openrouter            {o_ign}/{n} = {o_ign/n:.1%}")
    delta = (o_ign - q_ign) / n
    print(f"  delta                 {delta:+.1%}"
          f"   -> ~{abs(delta)*180:.0f} {'fewer' if delta > 0 else 'more'} posts/day at 180 entries/day")

    # Where the user overrode the AI, `category` is human ground truth.
    edited = [r for r in results if r["user_edited"] and r["category"] != r["original_category"]]
    if edited:
        q_ok = sum(r["original_category"] == r["category"] for r in edited)
        o_ok = sum(r["or_category"] == r["category"] for r in edited)
        print(f"\nUser-corrected entries (real ground truth, n={len(edited)}):")
        print(f"  qwen3.5 matched the user's label   {q_ok}/{len(edited)}")
        print(f"  openrouter matched the user's label {o_ok}/{len(edited)}")

    print("\nPer-category agreement (by qwen3.5's label):")
    by_cat = collections.defaultdict(lambda: [0, 0])
    for r in results:
        s = by_cat[r["original_category"]]
        s[1] += 1
        if r["or_category"] == r["original_category"]:
            s[0] += 1
    print(f"  {'qwen3.5 label':<26} {'agree':>12}")
    for cat, (ok, tot) in sorted(by_cat.items(), key=lambda kv: -kv[1][1]):
        print(f"  {cat:<26} {ok:>4}/{tot:<4} {ok/tot:>6.0%}")

    print("\nMost common disagreements (qwen3.5 -> openrouter):")
    conf = collections.Counter((r["original_category"], r["or_category"])
                               for r in results if r["or_category"] != r["original_category"])
    for (a, b), c in conf.most_common(12):
        print(f"  {c:>3}x  {a:<26} -> {b}")

    if args.rate:
        scored = [r for r in results if r.get("newsworthiness_score") is not None]
        if scored:
            m_q = sum(r["newsworthiness_score"] for r in scored) / len(scored)
            m_o = sum(r["or_score"] for r in scored) / len(scored)
            mad = sum(abs(r["or_score"] - r["newsworthiness_score"]) for r in scored) / len(scored)
            gate = config.NEWSWORTHINESS_THRESHOLD
            pq = sum(r["newsworthiness_score"] >= gate for r in scored)
            po = sum(r["or_score"] >= gate for r in scored)
            print(f"\nNewsworthiness (n={len(scored)}):")
            print(f"  mean  qwen3.5 {m_q:.2f}  vs  openrouter {m_o:.2f}   (mean abs diff {mad:.2f})")
            print(f"  pass gate {gate}:  qwen3.5 {pq}/{len(scored)} ({pq/len(scored):.0%})   "
                  f"openrouter {po}/{len(scored)} ({po/len(scored):.0%})")

    disagreements = [
        {"content": r["content"][:200], "qwen35": r["original_category"],
         "openrouter": r["or_category"], "final_label": r["category"],
         "user_edited": bool(r["user_edited"]), "openrouter_reasoning": r["or_reasoning"]}
        for r in results if r["or_category"] != r["original_category"]
    ]
    print(f"\n--- sample disagreements (first 12 of {len(disagreements)}) ---")
    for d in disagreements[:12]:
        print(f"\n  qwen3.5={d['qwen35']!r}  openrouter={d['openrouter']!r}")
        print(f"    {' '.join(d['content'].split())[:150]}")

    os.makedirs(RUNS_DIR, exist_ok=True)
    out = os.path.join(RUNS_DIR, time.strftime("%Y%m%dT%H%M%SZ") + "_backend_compare.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "model": config.OPENROUTER_CATEGORIZATION_MODEL,
            "n": n, "agreement": agree / n,
            "qwen35_ignore_rate": q_ign / n, "openrouter_ignore_rate": o_ign / n,
            "per_category": {k: {"agree": v[0], "total": v[1]} for k, v in by_cat.items()},
            "disagreements": disagreements,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
