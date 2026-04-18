"""Undo a tuning commit, or revert config.py to the pre-tuning baseline.

Modes:
    --list                  Show last N commits on the tuning branch, with macro-F1 deltas.
    --to-commit <sha>       `git reset --hard <sha>` on the tuning branch.
    --to-baseline           Revert config.py (on the current branch) to its state at
                            the commit right before the most recent tuning commit.

All destructive git operations are gated behind --yes. Without --yes the
script prints the plan and exits (dry-run by default). Matches the project's
"measure twice, cut once" rule.
"""
import argparse
import json
import os
import subprocess
import sys

from _paths import CONFIG_PATH, HISTORY_PATH, PROJECT_ROOT

TUNING_BRANCH = "tuning"


def git(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        check=check,
        text=True,
        capture_output=capture,
    )


def load_history() -> list[dict]:
    if not os.path.exists(HISTORY_PATH):
        return []
    out = []
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def macro_f1_for_sha(sha: str, history: list[dict]) -> float | None:
    for entry in history:
        if entry.get("commit_sha") == sha:
            for p in entry.get("proposals", []):
                if p.get("name") == entry.get("winner"):
                    return p.get("macro_f1")
    return None


def cmd_list(n: int):
    try:
        out = git("log", f"-{n}", "--format=%H|%ai|%s", TUNING_BRANCH, capture=True).stdout
    except subprocess.CalledProcessError:
        print(f"Branch '{TUNING_BRANCH}' does not exist yet.", file=sys.stderr)
        return

    history = load_history()
    lines = [ln for ln in out.strip().split("\n") if ln]
    if not lines:
        print("No tuning commits found.")
        return

    print(f"{'commit':<12} {'date':<22} {'macro-F1':<10} subject")
    for ln in lines:
        sha, date, subject = ln.split("|", 2)
        f1 = macro_f1_for_sha(sha, history)
        f1_str = f"{f1:.4f}" if f1 is not None else "?"
        print(f"{sha[:10]:<12} {date[:19]:<22} {f1_str:<10} {subject}")


def cmd_to_commit(sha: str, yes: bool):
    current = git("rev-parse", "--abbrev-ref", "HEAD", capture=True).stdout.strip()
    print(f"Plan:")
    print(f"  git checkout {TUNING_BRANCH}")
    print(f"  git reset --hard {sha}")
    if not yes:
        print("\n(dry-run; rerun with --yes to execute)")
        return
    git("checkout", TUNING_BRANCH)
    git("reset", "--hard", sha)
    print(f"Reset '{TUNING_BRANCH}' to {sha}. Original branch was '{current}'.")


def cmd_to_baseline(yes: bool):
    """Restore config.py to the blob it had at the commit just *before*
    the most recent tuning commit."""
    try:
        tuning_head = git("rev-parse", TUNING_BRANCH, capture=True).stdout.strip()
    except subprocess.CalledProcessError:
        print(f"Branch '{TUNING_BRANCH}' does not exist.", file=sys.stderr)
        sys.exit(1)

    # Parent of the most recent tuning commit.
    parent = git("rev-parse", f"{tuning_head}^", capture=True).stdout.strip()

    current_branch = git("rev-parse", "--abbrev-ref", "HEAD", capture=True).stdout.strip()

    print(f"Plan:")
    print(f"  Current branch: {current_branch}")
    print(f"  Restoring config.py to its state at {parent[:10]} "
          f"(parent of {tuning_head[:10]})")
    print(f"  Command: git checkout {parent} -- config.py")
    print(f"  Then: git add config.py (leaves it staged for you to commit/revert)")
    if not yes:
        print("\n(dry-run; rerun with --yes to execute)")
        return
    git("checkout", parent, "--", "config.py")
    git("add", "config.py")
    print(f"Restored config.py from {parent[:10]}. Review `git diff --cached` "
          "and commit if you want to persist the rollback.")


def main():
    parser = argparse.ArgumentParser(description="Rollback tuning changes.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List recent tuning commits.")
    group.add_argument("--to-commit", metavar="SHA",
                       help="Reset the tuning branch to this commit.")
    group.add_argument("--to-baseline", action="store_true",
                       help="Restore config.py to pre-tuning state on current branch.")
    parser.add_argument("-n", type=int, default=20, help="How many commits to list.")
    parser.add_argument("--yes", action="store_true",
                        help="Actually execute. Without this flag, only the plan is printed.")
    args = parser.parse_args()

    if args.list:
        cmd_list(args.n)
    elif args.to_commit:
        cmd_to_commit(args.to_commit, args.yes)
    elif args.to_baseline:
        cmd_to_baseline(args.yes)


if __name__ == "__main__":
    main()
