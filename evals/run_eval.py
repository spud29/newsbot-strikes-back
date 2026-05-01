"""Run the current (or overlaid) categorizer against the golden set.

A *variant* is a JSON file with any subset of:
    {"system_prompt": "...", "duplicate_threshold": 0.95, "similarity_threshold": 0.75}

Before instantiating OllamaClient, we monkey-patch `config.*` with those
overrides. No file writes, no persistent changes. Baseline run = no overlay.

Produces:
    - evals/runs/<timestamp>_<variant_id>.json  (full metrics JSON)
    - one appended line to evals/history.jsonl   (compact summary)
    - stdout table                               (human read)
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

from _paths import (
    GOLDEN_PATH, HISTORY_PATH, RUNS_DIR, ensure_project_on_syspath,
)

ensure_project_on_syspath()


def load_overlay(path: str | None) -> dict:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    allowed = {"system_prompt", "duplicate_threshold", "similarity_threshold",
               "newsworthiness_threshold"}
    extra = set(data) - allowed - {"name", "notes", "rationale"}
    if extra:
        raise ValueError(f"variant overlay has disallowed keys: {extra}")
    return data


def apply_overlay(overlay: dict) -> str:
    """Mutate `config` module attributes from the overlay. Returns a config hash."""
    import config
    if "system_prompt" in overlay:
        config.SYSTEM_PROMPT = overlay["system_prompt"]
    if "duplicate_threshold" in overlay:
        config.DUPLICATE_THRESHOLD = float(overlay["duplicate_threshold"])
    if "similarity_threshold" in overlay:
        config.SIMILARITY_THRESHOLD = float(overlay["similarity_threshold"])
    if "newsworthiness_threshold" in overlay:
        config.NEWSWORTHINESS_THRESHOLD = float(overlay["newsworthiness_threshold"])
    h = hashlib.sha256()
    h.update(config.SYSTEM_PROMPT.encode("utf-8"))
    h.update(f"|{config.DUPLICATE_THRESHOLD}|{config.SIMILARITY_THRESHOLD}".encode("utf-8"))
    return h.hexdigest()[:12]


def load_golden(path: str, label_filter: str) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    if label_filter == "explicit":
        rows = [r for r in rows if r.get("label_confidence") == "explicit"]
    return rows


def compute_metrics(y_true: list[str], y_pred: list[str], labels: list[str]) -> dict:
    from sklearn.metrics import (
        precision_recall_fscore_support, confusion_matrix,
    )
    p, r, f, support = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, zero_division=0
    )
    per_category = {
        cat: {
            "precision": float(p[i]),
            "recall": float(r[i]),
            "f1": float(f[i]),
            "support": int(support[i]),
        }
        for i, cat in enumerate(labels)
    }
    # Macro-F1 over categories with support > 0 (empty classes would drag it to 0).
    supported_f1 = [per_category[c]["f1"] for c in labels if per_category[c]["support"] > 0]
    macro_f1 = sum(supported_f1) / len(supported_f1) if supported_f1 else 0.0
    cm = confusion_matrix(y_true, y_pred, labels=labels).tolist()
    return {"macro_f1": macro_f1, "per_category": per_category, "confusion": cm}


def print_stdout_report(metrics: dict, labels: list[str], n_examples: int,
                        variant_id: str, wall_time: float):
    print()
    print(f"== Eval: {variant_id} ==")
    print(f"Examples: {n_examples}   wall: {wall_time:.1f}s   macro-F1: {metrics['macro_f1']:.4f}")
    print()
    print(f"{'category':<22} {'P':>6} {'R':>6} {'F1':>6} {'n':>5}")
    for cat in labels:
        s = metrics["per_category"][cat]
        if s["support"] == 0:
            continue
        print(f"{cat:<22} {s['precision']:>6.3f} {s['recall']:>6.3f} {s['f1']:>6.3f} {s['support']:>5}")
    print()


def append_history(entry: dict):
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_categorization(rows: list[dict]) -> list[str]:
    """Call the live categorizer on each row and return predictions."""
    from ollama_client import OllamaClient
    from database import Database

    db = Database()
    # Pass database so live behavior (ignore-example enhancement) is included.
    # Do NOT pass removed_entries_db — that feedback layer is orthogonal to prompt tuning.
    ollama = OllamaClient(database=db)

    try:
        from tqdm import tqdm
        iterator = tqdm(rows, desc="categorizing", unit="ex")
    except ImportError:
        iterator = rows

    preds: list[str] = []
    for row in iterator:
        try:
            cat, _reasoning = ollama.categorize(row["content"])
        except Exception as e:
            print(f"  error on {row.get('entry_id')}: {e}", file=sys.stderr)
            cat = "ignore"  # conservative fallback matches live-bot error path
        preds.append(cat)
    return preds


def main():
    parser = argparse.ArgumentParser(description="Run categorization eval against golden set.")
    parser.add_argument("--variant", help="Path to variant JSON overlay (omit for baseline).")
    parser.add_argument("--golden", default=GOLDEN_PATH)
    parser.add_argument("--out", help="Output report JSON path (default: runs/<ts>_<id>.json).")
    parser.add_argument("--label", choices=["all", "explicit"], default="all")
    parser.add_argument("--limit", type=int, default=0, help="Smoke-test: only run N examples (0=all).")
    args = parser.parse_args()

    overlay = load_overlay(args.variant)
    config_hash = apply_overlay(overlay)

    variant_id = overlay.get("name") or (
        os.path.splitext(os.path.basename(args.variant))[0] if args.variant else "baseline"
    )

    rows = load_golden(args.golden, args.label)
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("ERROR: golden set is empty after filter.", file=sys.stderr)
        sys.exit(2)

    from config import VALID_CATEGORIES  # pull after overlay so it's the patched module
    labels = list(VALID_CATEGORIES)

    started = time.time()
    y_pred = run_categorization(rows)
    y_true = [r["expected_category"] for r in rows]
    wall_time = time.time() - started

    metrics = compute_metrics(y_true, y_pred, labels)
    print_stdout_report(metrics, labels, len(rows), variant_id, wall_time)

    timestamp_iso = datetime.now(timezone.utc).isoformat()
    ts_slug = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    out_path = args.out or os.path.join(RUNS_DIR, f"{ts_slug}_{variant_id}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    report = {
        "variant_id": variant_id,
        "variant_path": args.variant,
        "timestamp": timestamp_iso,
        "n_examples": len(rows),
        "label_filter": args.label,
        "config_hash": config_hash,
        "wall_time_sec": round(wall_time, 2),
        "macro_f1": metrics["macro_f1"],
        "per_category": metrics["per_category"],
        "confusion": metrics["confusion"],
        "confusion_labels": labels,
        # Store per-example predictions so the tuning agent can analyze misclassifications.
        "predictions": [
            {"entry_id": r["entry_id"], "expected": t, "predicted": p,
             "content": r["content"], "label_confidence": r.get("label_confidence")}
            for r, t, p in zip(rows, y_true, y_pred)
        ],
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"Wrote {out_path}")

    append_history({
        "timestamp": timestamp_iso,
        "variant_id": variant_id,
        "config_hash": config_hash,
        "n_examples": len(rows),
        "macro_f1": metrics["macro_f1"],
        "per_category_f1": {c: metrics["per_category"][c]["f1"] for c in labels},
        "report_path": os.path.relpath(out_path, os.path.dirname(HISTORY_PATH)),
    })


if __name__ == "__main__":
    main()
