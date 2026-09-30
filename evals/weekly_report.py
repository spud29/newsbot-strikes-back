"""Weekly trend report.

Reads evals/history.jsonl, emits a markdown file with three matplotlib charts:
 1. Macro-F1 over time — accepted variants green-dotted, rejected gray-dotted.
 2. Per-category F1 over time — small-multiples grid.
 3. Acceptance rate bar — per-day proposals vs accepted.

Run on Sundays via Windows Task Scheduler; manual invocation supported.
"""
import argparse
import datetime as dt
import json
import os
from collections import defaultdict

from _paths import HISTORY_PATH, REPORTS_DIR, REPORTS_IMG_DIR


def load_history(history_path: str) -> list[dict]:
    if not os.path.exists(history_path):
        return []
    out = []
    with open(history_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def parse_ts(s: str) -> dt.datetime:
    # History entries always use ISO-8601 with tzinfo; be lenient.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return dt.datetime.fromisoformat(s)


def filter_window(history: list[dict], days: int) -> list[dict]:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    return [h for h in history if parse_ts(h["timestamp"]) >= cutoff]


def chart_macro_f1(history: list[dict], out_path: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    evals = [h for h in history if "macro_f1" in h]
    if not evals:
        return False

    evals.sort(key=lambda h: parse_ts(h["timestamp"]))
    xs = [parse_ts(h["timestamp"]) for h in evals]
    ys = [h["macro_f1"] for h in evals]
    colors = []
    for h in evals:
        vid = h.get("variant_id", "")
        if vid == "baseline":
            colors.append("black")
        else:
            # Determine if this variant was accepted (appeared as a winner with commit_sha).
            colors.append("gray")

    # Overlay accepted cycle-log points in green.
    cycle_wins = [h for h in history if h.get("cycle") == "complete" and h.get("commit_sha")]
    win_xs = [parse_ts(h["timestamp"]) for h in cycle_wins]
    win_ys = []
    for h in cycle_wins:
        for p in h.get("proposals", []):
            if p["name"] == h["winner"]:
                win_ys.append(p["macro_f1"])
                break
        else:
            win_ys.append(None)

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(xs, ys, color="#888", linewidth=0.8, zorder=1)
    ax.scatter(xs, ys, c=colors, s=28, zorder=2)
    if win_xs:
        ax.scatter(win_xs, [y for y in win_ys if y is not None],
                   c="#2aa02a", s=80, marker="*", zorder=3, label="accepted variant")
        ax.legend(loc="lower right")
    ax.set_title("Macro-F1 over time")
    ax.set_ylabel("macro-F1")
    ax.set_ylim(0, 1)
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return True


def chart_per_category(history: list[dict], out_path: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    evals = [h for h in history if h.get("per_category_f1")]
    if not evals:
        return False
    evals.sort(key=lambda h: parse_ts(h["timestamp"]))

    categories = sorted({c for h in evals for c in h["per_category_f1"]})
    n = len(categories)
    cols = 4
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 3, rows * 2), sharey=True)
    axes = axes.flatten() if n > 1 else [axes]

    xs = [parse_ts(h["timestamp"]) for h in evals]
    for i, cat in enumerate(categories):
        ax = axes[i]
        ys = [h["per_category_f1"].get(cat, 0.0) for h in evals]
        ax.plot(xs, ys, color="#1f77b4", linewidth=1)
        ax.set_title(cat, fontsize=9)
        ax.set_ylim(0, 1)
        ax.tick_params(axis="x", rotation=30, labelsize=7)
        ax.tick_params(axis="y", labelsize=7)
        ax.grid(alpha=0.3)

    for j in range(n, len(axes)):
        axes[j].axis("off")

    fig.suptitle("Per-category F1 over time", fontsize=12)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return True


def chart_acceptance(history: list[dict], out_path: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cycles = [h for h in history if h.get("cycle") == "complete"]
    if not cycles:
        return False

    by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"proposed": 0, "accepted": 0})
    for h in cycles:
        day = parse_ts(h["timestamp"]).strftime("%Y-%m-%d")
        by_day[day]["proposed"] += len(h.get("proposals", []))
        if h.get("commit_sha"):
            by_day[day]["accepted"] += 1

    days = sorted(by_day.keys())
    proposed = [by_day[d]["proposed"] for d in days]
    accepted = [by_day[d]["accepted"] for d in days]

    fig, ax = plt.subplots(figsize=(10, 3))
    x = range(len(days))
    ax.bar(x, proposed, color="#cccccc", label="proposed")
    ax.bar(x, accepted, color="#2aa02a", label="accepted")
    ax.set_xticks(list(x))
    ax.set_xticklabels(days, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("count")
    ax.set_title("Variant proposals vs acceptances")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return True


def build_markdown(history: list[dict], chart_paths: dict[str, str],
                   out_path: str, days: int):
    evals = [h for h in history if "macro_f1" in h]
    cycles = [h for h in history if h.get("cycle") == "complete"]
    accepted = [h for h in cycles if h.get("commit_sha")]

    if evals:
        latest = evals[-1]
        first = evals[0]
        f1_delta = latest["macro_f1"] - first["macro_f1"]
    else:
        latest = first = None
        f1_delta = 0.0

    # Top-3 regressions (any category where latest F1 dropped vs first).
    regressions = []
    if evals and evals[0].get("per_category_f1") and evals[-1].get("per_category_f1"):
        for cat in evals[-1]["per_category_f1"]:
            d = evals[-1]["per_category_f1"][cat] - evals[0]["per_category_f1"].get(cat, 0.0)
            regressions.append((cat, d))
        regressions.sort(key=lambda kv: kv[1])

    lines = [
        f"# Categorization tuning — week of {dt.date.today().isoformat()}",
        "",
        f"_Window: last {days} days. Entries in history: {len(history)}._",
        "",
        "## Summary",
        "",
    ]
    if latest:
        lines += [
            f"- Latest macro-F1: **{latest['macro_f1']:.4f}** ({latest['variant_id']})",
            f"- First in window: {first['macro_f1']:.4f} ({first['variant_id']})",
            f"- Delta over window: **{f1_delta:+.4f}**",
        ]
    lines += [
        f"- Tuning cycles run: {len(cycles)}  |  variants accepted: {len(accepted)}",
        "",
    ]

    if chart_paths.get("macro"):
        lines += ["## Macro-F1 trend", "",
                  f"![macro-F1]({os.path.relpath(chart_paths['macro'], os.path.dirname(out_path))})",
                  ""]
    if chart_paths.get("per_cat"):
        lines += ["## Per-category F1", "",
                  f"![per-category]({os.path.relpath(chart_paths['per_cat'], os.path.dirname(out_path))})",
                  ""]
    if chart_paths.get("accept"):
        lines += ["## Acceptance rate", "",
                  f"![acceptance]({os.path.relpath(chart_paths['accept'], os.path.dirname(out_path))})",
                  ""]

    if accepted:
        lines += ["## Accepted tuning commits this window", ""]
        for h in accepted:
            sha = h.get("commit_sha", "")
            win = h.get("winner", "")
            # Pull winner F1 from proposals.
            f1 = None
            for p in h.get("proposals", []):
                if p["name"] == win:
                    f1 = p["macro_f1"]
                    break
            lines.append(f"- `{sha[:10]}` — **{win}** (macro-F1 {f1:.4f})"
                         if f1 is not None else f"- `{sha[:10]}` — {win}")
        lines.append("")

    if regressions:
        worst = [r for r in regressions if r[1] < 0][:3]
        if worst:
            lines += ["## Top regressions to watch", ""]
            for cat, d in worst:
                lines.append(f"- **{cat}**: F1 {d:+.3f}")
            lines.append("")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Generate weekly eval report.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--history", default=HISTORY_PATH)
    parser.add_argument("--out-dir", default=REPORTS_DIR)
    args = parser.parse_args()

    history_all = load_history(args.history)
    history = filter_window(history_all, args.days)

    if not history:
        print("No history entries in window. Nothing to report.")
        return

    os.makedirs(REPORTS_IMG_DIR, exist_ok=True)
    today = dt.date.today().isoformat()
    macro_png = os.path.join(REPORTS_IMG_DIR, f"{today}_macro_f1.png")
    per_cat_png = os.path.join(REPORTS_IMG_DIR, f"{today}_per_category.png")
    accept_png = os.path.join(REPORTS_IMG_DIR, f"{today}_acceptance.png")

    chart_paths = {}
    if chart_macro_f1(history, macro_png):
        chart_paths["macro"] = macro_png
    if chart_per_category(history, per_cat_png):
        chart_paths["per_cat"] = per_cat_png
    if chart_acceptance(history, accept_png):
        chart_paths["accept"] = accept_png

    md_path = os.path.join(args.out_dir, f"week_{today}.md")
    build_markdown(history, chart_paths, md_path, args.days)
    print(f"Wrote {md_path}")
    for k, v in chart_paths.items():
        print(f"  + {k}: {v}")


if __name__ == "__main__":
    main()
