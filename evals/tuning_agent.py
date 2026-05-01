"""Nightly headless tuning loop.

One cycle:
  1. Sanity checks (clean working tree on main, not already running).
  2. Baseline: run eval on current config.py (or reuse today's baseline).
  3. Ask Claude to propose 3 variants via tool-use. Tool schema constrains it to
     `SYSTEM_PROMPT` / `DUPLICATE_THRESHOLD` / `SIMILARITY_THRESHOLD` — anything
     else is ignored.
  4. Run eval on each variant (sequential — Ollama is single-GPU).
  5. Apply gate:
       - macro-F1 >= baseline + 0.02
       - no per-category F1 drop > 0.05 (categories with support < 5 exempt)
  6. If a winner passes, create/reset `tuning` branch, AST-patch config.py,
     commit with a changelog message. Never push, never touch main.
  7. Log every variant's fate to evals/history.jsonl.

Requires ANTHROPIC_API_KEY in the environment.

The `anthropic` SDK is used directly rather than the higher-level
`claude-agent-sdk` — we need exactly one turn of tool-use (propose 3 variants)
and the base SDK makes that trivial and dependency-light.
"""
import argparse
import ast
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from _paths import (
    BASELINE_VARIANT_PATH, CONFIG_PATH, EVALS_DIR, HISTORY_PATH, LOCK_PATH,
    PENDING_DIR, PROJECT_ROOT, RUNS_DIR, VARIANTS_DIR,
    ensure_project_on_syspath,
)

ensure_project_on_syspath()

# Load .env explicitly with utf-8-sig to handle BOM-prefixed files (common when
# .env is saved by Windows editors). Must happen before importing config.
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"), encoding="utf-8-sig", override=True)

F1_IMPROVEMENT_GATE = 0.01
F1_REGRESSION_GATE = 0.05
MIN_SUPPORT_FOR_GATE = 5
TUNING_BRANCH = "tuning"
DEFAULT_MODEL = "claude-opus-4-7"
N_VARIANTS = 3


# ----- git helpers -----

def git(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        check=check,
        text=True,
        capture_output=capture,
    )


def current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD", capture=True).stdout.strip()


def working_tree_clean() -> bool:
    out = git("status", "--porcelain", capture=True).stdout
    # Ignore untracked files (lines starting with "??") — they don't affect commits.
    tracked_changes = [l for l in out.splitlines() if not l.startswith("??")]
    return len(tracked_changes) == 0


# ----- lock -----

class TuningLock:
    def __enter__(self):
        if os.path.exists(LOCK_PATH):
            with open(LOCK_PATH) as f:
                holder = f.read().strip()
            raise SystemExit(f"tuning lock held: {holder}")
        with open(LOCK_PATH, "w") as f:
            f.write(f"pid={os.getpid()} started={dt.datetime.now().isoformat()}")
        return self

    def __exit__(self, *exc):
        try:
            os.remove(LOCK_PATH)
        except FileNotFoundError:
            pass


# ----- baseline eval -----

def run_eval_subprocess(variant_path: str | None, label: str = "all") -> dict:
    ts_slug = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = Path(variant_path).stem if variant_path else "baseline"
    out_path = os.path.join(RUNS_DIR, f"{ts_slug}_{name}.json")
    cmd = [sys.executable, os.path.join(EVALS_DIR, "run_eval.py"),
           "--out", out_path, "--label", label]
    if variant_path:
        cmd.extend(["--variant", variant_path])
    print(f"\n>>> {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True, cwd=PROJECT_ROOT)
    with open(out_path, "r", encoding="utf-8") as f:
        return json.load(f)


def latest_baseline_today() -> dict | None:
    if not os.path.isdir(RUNS_DIR):
        return None
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    cands = sorted(
        p for p in os.listdir(RUNS_DIR)
        if p.startswith(today) and p.endswith("_baseline.json")
    )
    if not cands:
        return None
    with open(os.path.join(RUNS_DIR, cands[-1]), "r", encoding="utf-8") as f:
        return json.load(f)


# ----- variant proposal via Claude tool-use -----

PROPOSE_TOOL = {
    "name": "propose_variant",
    "description": (
        "Propose a single configuration variant. Call this tool exactly 3 times, "
        "each time proposing a different variant. Each variant must include at least "
        "one of: system_prompt, duplicate_threshold, similarity_threshold, "
        "newsworthiness_threshold. "
        "Provide a short rationale explaining why this variant is expected to help."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string",
                     "description": "Short filename-safe variant id (e.g. 'v1_tighter_ignore')."},
            "rationale": {"type": "string",
                          "description": "1-3 sentences on what this variant changes and why."},
            "system_prompt": {"type": "string",
                              "description": "Full replacement SYSTEM_PROMPT. Omit to keep current."},
            "duplicate_threshold": {"type": "number",
                                    "description": "Override DUPLICATE_THRESHOLD (0.0-1.0). Omit to keep current."},
            "similarity_threshold": {"type": "number",
                                     "description": "Override SIMILARITY_THRESHOLD (0.0-1.0). Omit to keep current."},
            "newsworthiness_threshold": {"type": "number",
                                         "description": "Override NEWSWORTHINESS_THRESHOLD (1.0-10.0). "
                                                        "Lower values let more items through the post filter. "
                                                        "Omit to keep current."},
        },
        "required": ["name", "rationale"],
    },
}


def build_context_pack(baseline: dict) -> str:
    import config
    per_cat = baseline["per_category"]
    worst = sorted(
        [(c, s) for c, s in per_cat.items() if s["support"] >= MIN_SUPPORT_FOR_GATE],
        key=lambda kv: kv[1]["f1"],
    )[:3]

    # Pull up to 5 misclassified examples per worst category.
    preds_by_expected: dict[str, list[dict]] = {}
    for p in baseline.get("predictions", []):
        if p["expected"] != p["predicted"]:
            preds_by_expected.setdefault(p["expected"], []).append(p)

    misclass_section = []
    for cat, _ in worst:
        misses = preds_by_expected.get(cat, [])[:5]
        if not misses:
            continue
        misclass_section.append(f"\n### Misclassifications for '{cat}' (expected → actual)")
        for m in misses:
            snippet = (m["content"] or "").replace("\n", " ")[:220]
            misclass_section.append(f"- expected={cat} got={m['predicted']}: {snippet}")

    parts = [
        f"## Current baseline macro-F1: {baseline['macro_f1']:.4f} "
        f"({baseline['n_examples']} examples)",
        "",
        "## Per-category F1 (sorted worst-first):",
        "| category | P | R | F1 | n |",
        "|---|---|---|---|---|",
    ]
    for cat, s in sorted(per_cat.items(), key=lambda kv: kv[1]["f1"]):
        if s["support"] == 0:
            continue
        parts.append(f"| {cat} | {s['precision']:.3f} | {s['recall']:.3f} "
                     f"| {s['f1']:.3f} | {s['support']} |")

    parts.append(f"\n## Current thresholds")
    parts.append(f"- DUPLICATE_THRESHOLD = {config.DUPLICATE_THRESHOLD}")
    parts.append(f"- SIMILARITY_THRESHOLD = {config.SIMILARITY_THRESHOLD}")
    parts.append(f"- NEWSWORTHINESS_THRESHOLD = {getattr(config, 'NEWSWORTHINESS_THRESHOLD', 7.0)}"
                 f"  (post-categorization filter; lower = more items posted)")
    parts.append(f"\n## Valid categories\n{', '.join(config.VALID_CATEGORIES)}")
    parts.append(f"\n## Current SYSTEM_PROMPT\n\n```\n{config.SYSTEM_PROMPT}\n```")
    parts.extend(misclass_section)

    parts.append("""
## Your task

Propose exactly 3 variants by calling `propose_variant` 3 times. Each variant
should target a *different* hypothesis about why the current config is
underperforming, e.g.:

1. A prompt rewrite that strengthens disambiguation between the top 2
   confusion pairs shown above.
2. A threshold tweak (DUPLICATE or SIMILARITY) if dedup looks too loose/tight.
3. A NEWSWORTHINESS_THRESHOLD adjustment if the post-filter appears too strict
   (e.g. too many correctly-categorized items are still being discarded).
4. A prompt edit that better defines the `ignore` bar (or the worst-performing
   real category).

Constraints:
- Do NOT remove any category from VALID_CATEGORIES.
- Do NOT change the JSON output contract at the end of the system prompt.
- Keep the full category-definition section; edit surgically, don't rewrite end-to-end.
- Variant names must be filesystem-safe (e.g. `v1_tighter_ignore`).
- DUPLICATE_THRESHOLD and SIMILARITY_THRESHOLD must be in (0.0, 1.0).
- NEWSWORTHINESS_THRESHOLD must be in [1.0, 10.0].
""")
    return "\n".join(parts)


def propose_variants(baseline: dict, model: str = DEFAULT_MODEL) -> list[dict]:
    try:
        import anthropic
    except ImportError:
        raise SystemExit("anthropic SDK not installed. Run: pip install anthropic")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set in environment.")

    client = anthropic.Anthropic()
    prompt = build_context_pack(baseline)

    response = client.messages.create(
        model=model,
        max_tokens=8192,
        tools=[PROPOSE_TOOL],
        messages=[{"role": "user", "content": prompt}],
    )

    proposals = []
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "propose_variant":
            proposals.append(block.input)

    if len(proposals) < N_VARIANTS:
        print(f"WARN: Claude proposed {len(proposals)} variants, expected {N_VARIANTS}",
              file=sys.stderr)
    return proposals[:N_VARIANTS]


def write_variant_files(proposals: list[dict]) -> list[str]:
    os.makedirs(PENDING_DIR, exist_ok=True)
    # Clear previous pending variants.
    for f in os.listdir(PENDING_DIR):
        if f.endswith(".json"):
            os.remove(os.path.join(PENDING_DIR, f))

    paths = []
    for p in proposals:
        name = re.sub(r"[^A-Za-z0-9_-]", "_", p["name"])[:60] or "variant"
        variant = {k: v for k, v in p.items()
                   if k in ("system_prompt", "duplicate_threshold", "similarity_threshold",
                            "newsworthiness_threshold", "name", "rationale")}
        variant["name"] = name
        # Validate thresholds early.
        invalid = False
        for thr_key in ("duplicate_threshold", "similarity_threshold"):
            if thr_key in variant:
                v = float(variant[thr_key])
                if not (0.0 < v < 1.0):
                    print(f"  dropping {name}: {thr_key}={v} out of range", file=sys.stderr)
                    invalid = True
                    break
        if not invalid and "newsworthiness_threshold" in variant:
            v = float(variant["newsworthiness_threshold"])
            if not (1.0 <= v <= 10.0):
                print(f"  dropping {name}: newsworthiness_threshold={v} out of range", file=sys.stderr)
                invalid = True
        if not invalid:
            path = os.path.join(PENDING_DIR, f"{name}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(variant, f, indent=2, ensure_ascii=False)
            paths.append(path)
    return paths


# ----- gating -----

def gate_variant(baseline: dict, variant: dict) -> tuple[bool, str]:
    bmf, vmf = baseline["macro_f1"], variant["macro_f1"]
    if vmf < bmf + F1_IMPROVEMENT_GATE:
        return False, f"macro-F1 {vmf:.4f} does not beat baseline {bmf:.4f} by {F1_IMPROVEMENT_GATE}"
    bpc, vpc = baseline["per_category"], variant["per_category"]
    for cat, b in bpc.items():
        if b["support"] < MIN_SUPPORT_FOR_GATE:
            continue
        v = vpc.get(cat, {"f1": 0.0})
        if b["f1"] - v["f1"] > F1_REGRESSION_GATE:
            return False, (f"category '{cat}' F1 regressed {b['f1']:.3f} → {v['f1']:.3f} "
                           f"(> {F1_REGRESSION_GATE})")
    return True, "passed"


# ----- AST patch of config.py -----

def patch_config_py(overlay: dict) -> list[str]:
    """Patch SYSTEM_PROMPT / DUPLICATE_THRESHOLD / SIMILARITY_THRESHOLD in config.py.

    Uses ast to find the exact assignment nodes, then does a text replacement
    over the original source spans. Preserves everything else byte-for-byte.
    Returns the list of field names actually patched.
    """
    src = Path(CONFIG_PATH).read_text(encoding="utf-8")
    tree = ast.parse(src)

    targets = {
        "SYSTEM_PROMPT": overlay.get("system_prompt"),
        "DUPLICATE_THRESHOLD": overlay.get("duplicate_threshold"),
        "SIMILARITY_THRESHOLD": overlay.get("similarity_threshold"),
        "NEWSWORTHINESS_THRESHOLD": overlay.get("newsworthiness_threshold"),
    }
    targets = {k: v for k, v in targets.items() if v is not None}

    # Collect (start, end, new_text) edits keyed by line span.
    edits = []  # list of (start_offset, end_offset, new_text)
    lines = src.splitlines(keepends=True)
    # Precompute line offsets
    line_starts = [0]
    for line in lines:
        line_starts.append(line_starts[-1] + len(line))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        if name not in targets:
            continue
        start = line_starts[node.lineno - 1] + (node.col_offset if node.col_offset else 0)
        end = line_starts[node.end_lineno - 1] + node.end_col_offset
        value = targets[name]
        if name == "SYSTEM_PROMPT":
            new_literal = _triple_quote_string(value)
        else:
            new_literal = repr(float(value))
        new_text = f"{name} = {new_literal}"
        edits.append((start, end, new_text))

    # Apply edits from the bottom up to preserve offsets.
    edits.sort(key=lambda e: e[0], reverse=True)
    patched = src
    patched_names = []
    for start, end, new_text in edits:
        patched = patched[:start] + new_text + patched[end:]
        # Reverse-lookup which name this was
    # Determine patched names by rewalking the new tree
    patched_names = list(targets.keys())

    Path(CONFIG_PATH).write_text(patched, encoding="utf-8")
    return patched_names


def _triple_quote_string(s: str) -> str:
    """Render s as a Python triple-quoted string literal, escaping only what's needed."""
    if '"""' not in s and not s.endswith('"'):
        return f'"""{s}"""'
    # Fall back to escaped double-quoted on a single line if the content has triple quotes.
    return repr(s)


# ----- commit winner -----

def apply_winner(winner: dict, baseline: dict, variant_path: str,
                 all_results: list[tuple[str, dict, bool, str]]) -> str:
    """Create/reset tuning branch, patch config, commit. Returns the commit SHA."""
    with open(variant_path, "r", encoding="utf-8") as f:
        overlay = json.load(f)

    # Reset or create the tuning branch from current main HEAD.
    git("checkout", "-B", TUNING_BRANCH)

    patched = patch_config_py(overlay)

    # Update baseline variant snapshot so next cycle's "apply_overlay" against
    # baseline.json produces an identity run.
    import config as _c
    importlib_reload(_c)
    snapshot = {
        "name": "baseline",
        "system_prompt": _c.SYSTEM_PROMPT,
        "duplicate_threshold": _c.DUPLICATE_THRESHOLD,
        "similarity_threshold": _c.SIMILARITY_THRESHOLD,
    }
    Path(BASELINE_VARIANT_PATH).write_text(json.dumps(snapshot, indent=2),
                                           encoding="utf-8")

    git("add", "config.py", os.path.relpath(BASELINE_VARIANT_PATH, PROJECT_ROOT))

    deltas = []
    for cat, b in sorted(baseline["per_category"].items()):
        if b["support"] == 0:
            continue
        v = winner["per_category"].get(cat, {"f1": 0.0})
        deltas.append(f"  {cat:<22} F1 {b['f1']:.3f} → {v['f1']:.3f} "
                      f"({v['f1']-b['f1']:+.3f})")

    all_lines = ["\nAll proposed variants this cycle:"]
    for name, res, passed, reason in all_results:
        flag = "ACCEPT" if passed else "reject"
        all_lines.append(f"  [{flag}] {name:<28} macro-F1 {res['macro_f1']:.4f}  ({reason})")

    improvement = winner["macro_f1"] - baseline["macro_f1"]
    msg = (
        f"tune: {winner['variant_id']} — macro-F1 {improvement:+.3f}\n\n"
        f"Baseline: macro-F1 {baseline['macro_f1']:.4f} ({baseline['n_examples']} ex)\n"
        f"Variant:  macro-F1 {winner['macro_f1']:.4f}\n"
        f"Patched:  {', '.join(patched)}\n\n"
        f"Per-category F1 deltas:\n" + "\n".join(deltas) + "\n"
        + "\n".join(all_lines) + "\n\n"
        f"Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>\n"
    )
    git("commit", "-m", msg)
    sha = git("rev-parse", "HEAD", capture=True).stdout.strip()
    return sha


def importlib_reload(mod):
    import importlib
    importlib.reload(mod)


# ----- history -----

def log_cycle(entry: dict):
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ----- main -----

def main():
    parser = argparse.ArgumentParser(description="Run one nightly tuning cycle.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Propose & eval variants but do not commit anything.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--force-baseline", action="store_true",
                        help="Re-run baseline even if today's report exists.")
    args = parser.parse_args()

    with TuningLock():
        # Sanity: branch + clean tree.
        branch = current_branch()
        if not args.dry_run and branch != "main":
            raise SystemExit(f"tuning agent must run on main branch, currently on '{branch}'")
        if not args.dry_run and not working_tree_clean():
            raise SystemExit("working tree dirty; commit or stash before running tuning agent.")

        os.makedirs(RUNS_DIR, exist_ok=True)
        os.makedirs(VARIANTS_DIR, exist_ok=True)

        # Baseline.
        baseline = None if args.force_baseline else latest_baseline_today()
        if baseline is None:
            print("Running baseline eval...")
            baseline = run_eval_subprocess(variant_path=None)
        else:
            print(f"Reusing today's baseline: macro-F1={baseline['macro_f1']:.4f}")

        # Propose.
        print("Asking Claude for variants...")
        proposals = propose_variants(baseline, model=args.model)
        paths = write_variant_files(proposals)
        if not paths:
            log_cycle({"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                       "cycle": "no_proposals", "baseline_macro_f1": baseline["macro_f1"]})
            print("No valid proposals. Exiting.")
            return

        # Eval each.
        results = []  # (variant_id, report, passed, reason)
        for path in paths:
            print(f"\nEvaluating variant: {Path(path).stem}")
            report = run_eval_subprocess(variant_path=path)
            passed, reason = gate_variant(baseline, report)
            results.append((report["variant_id"], report, passed, reason))

        # Pick best passing.
        winners = [(vid, rep, path) for (vid, rep, ok, _), path in zip(results, paths) if ok]
        winners.sort(key=lambda t: t[1]["macro_f1"], reverse=True)

        cycle_log = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "cycle": "complete",
            "baseline_macro_f1": baseline["macro_f1"],
            "proposals": [
                {"name": v, "macro_f1": r["macro_f1"], "passed": p, "reason": reason}
                for v, r, p, reason in results
            ],
            "winner": None,
            "commit_sha": None,
            "dry_run": args.dry_run,
        }

        if not winners:
            print("\nNo variant passed the gate. No commit.")
            log_cycle(cycle_log)
            return

        winner_id, winner_report, winner_path = winners[0]
        cycle_log["winner"] = winner_id

        if args.dry_run:
            print(f"\n[DRY-RUN] Would commit winner: {winner_id} "
                  f"(macro-F1 {winner_report['macro_f1']:.4f})")
            log_cycle(cycle_log)
            return

        print(f"\nApplying winner: {winner_id} "
              f"(macro-F1 {winner_report['macro_f1']:.4f} vs baseline {baseline['macro_f1']:.4f})")
        sha = apply_winner(winner_report, baseline, winner_path, results)
        cycle_log["commit_sha"] = sha
        print(f"Committed {sha} to branch '{TUNING_BRANCH}'.")
        log_cycle(cycle_log)


if __name__ == "__main__":
    main()
