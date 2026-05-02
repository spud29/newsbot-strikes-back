"""Cloud-based codebase improvement agent.

Runs in GitHub Actions (no Ollama available). Uses Claude API (claude-sonnet-4-6)
in two phases:

  Phase 1 — Planning:
    Claude receives the full codebase context and writes a ranked list of the
    top concrete improvements it intends to make, citing specific files and
    expected benefits.

  Phase 2 — Implementation:
    Claude receives its own plan plus the codebase and calls `propose_file_edit`
    once per file it wants to change.

After both phases the script:
  - Validates all edits (syntax-checks .py files via ast.parse)
  - Creates a branch  claude/auto-YYYYMMDD-HHMMSS
  - Commits the applied changes
  - Pushes and opens a pull request targeting main

Required environment variables:
  ANTHROPIC_API_KEY   — your Anthropic API key
  GH_TOKEN            — auto-provided as secrets.GITHUB_TOKEN in GitHub Actions
  GITHUB_REPOSITORY   — auto-provided (owner/repo)
"""

import ast
import datetime as dt
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────────────────

EVALS_DIR = Path(__file__).parent
PROJECT_ROOT = EVALS_DIR.parent

GOLDEN_PATH = EVALS_DIR / "categorization_golden.jsonl"
HISTORY_PATH = EVALS_DIR / "history.jsonl"

# ── constants ──────────────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"
MAX_TOKENS_PLAN = 4096
MAX_TOKENS_IMPL = 8192
GOLDEN_SAMPLE_SIZE = 20    # small balanced cross-section to stay well under rate limits
MAX_CHARS_PER_FILE = 8_000  # hard cap per source file (~2K tokens each)
PHASE_DELAY_SECS = 65       # wait between phases so the 1-minute rate-limit window resets

# Paths the agent must never write to (checked as prefix or exact basename)
BLOCKED_PREFIXES = (
    ".env",
    "bot.log",
    "data/",
    "data\\",
    "temp_media/",
    "temp_media\\",
    "evals/runs",
    "evals\\runs",
    "evals/pending",
    "evals\\pending",
    "evals/variants",
    "evals\\variants",
    "evals/history.jsonl",
    "evals\\history.jsonl",
)
BLOCKED_EXTENSIONS = {".session"}

# Source files included in the context pack (relative to PROJECT_ROOT).
# Keep this list short — every file is sent twice (planning + implementation)
# and each API call must stay under the org rate limit.
CONTEXT_FILES = [
    "config.py",          # SYSTEM_PROMPT, categories, all tunable thresholds
    "ollama_client.py",   # categorisation + embedding logic
    "main.py",            # orchestration, scheduler
    "discord_poster.py",  # posting + formatting
    "database.py",        # schema, queries
]

# ── tool schema ────────────────────────────────────────────────────────────────

EDIT_TOOL = {
    "name": "propose_file_edit",
    "description": (
        "Propose a modification to an existing file, or create a new file. "
        "Call this once per file. You may call it multiple times for different files. "
        "For edits: old_content must be an exact substring present in the current file. "
        "For new files: leave old_content empty."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Path relative to the project root, e.g. 'config.py' or "
                    "'evals/cloud_improve.py'."
                ),
            },
            "old_content": {
                "type": "string",
                "description": (
                    "Exact string to replace (copy-paste from the source shown above). "
                    "Leave empty or omit for new files."
                ),
            },
            "new_content": {
                "type": "string",
                "description": "Replacement text (or full file content for new files).",
            },
            "rationale": {
                "type": "string",
                "description": "1-3 sentences on why this change improves the codebase.",
            },
        },
        "required": ["file_path", "new_content", "rationale"],
    },
}

# ── context building ───────────────────────────────────────────────────────────

def _read_file(path: Path, max_chars: int = MAX_CHARS_PER_FILE) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... [truncated at {max_chars} chars]\n"
        return text
    except Exception as exc:
        return f"[could not read: {exc}]"


def _load_golden_sample(n: int = GOLDEN_SAMPLE_SIZE) -> list[dict]:
    if not GOLDEN_PATH.exists():
        return []
    all_samples: list[dict] = []
    with open(GOLDEN_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                all_samples.append(json.loads(line))

    # Build a balanced cross-section: up to (n // num_categories) per category.
    by_cat: dict[str, list[dict]] = {}
    for s in all_samples:
        by_cat.setdefault(s["expected_category"], []).append(s)

    per_cat = max(1, n // max(1, len(by_cat)))
    result: list[dict] = []
    for cat_samples in by_cat.values():
        result.extend(cat_samples[:per_cat])
    return result[:n]


def _load_history_summary() -> str:
    if not HISTORY_PATH.exists():
        return "No tuning history yet."
    entries: list[dict] = []
    with open(HISTORY_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    if not entries:
        return "No tuning history yet."

    lines = ["Recent tuning cycles (oldest first):"]
    for entry in entries[-10:]:
        ts = entry.get("timestamp", "?")[:10]
        if entry.get("cycle") == "complete":
            winner = entry.get("winner") or "none"
            f1 = entry.get("baseline_macro_f1", 0.0)
            lines.append(f"  {ts}  baseline_macro_F1={f1:.4f}  winner={winner}")
            for prop in entry.get("proposals", []):
                flag = "ACCEPT" if prop.get("passed") else "reject"
                reason = prop.get("reason", "")
                lines.append(
                    f"    [{flag}] {prop['name']}: F1={prop.get('macro_f1', 0):.4f}  {reason}"
                )
    return "\n".join(lines)


def build_context_pack() -> str:
    parts: list[str] = ["# Newsbot-Strikes-Back — Full Codebase Context\n"]

    parts.append("## Source files\n")
    for rel in CONTEXT_FILES:
        path = PROJECT_ROOT / rel
        content = _read_file(path) if path.exists() else "[file not found]"
        parts.append(f"### {rel}\n```python\n{content}\n```\n")

    parts.append("## Golden dataset sample (ground-truth categorization examples)\n")
    samples = _load_golden_sample()
    for s in samples:
        snippet = (s.get("content") or "").replace("\n", " ")[:200]
        parts.append(f"- [{s['expected_category']}] {snippet}")
    parts.append("")

    parts.append("## Local tuning history (Ollama-backed nightly runs)\n")
    parts.append(_load_history_summary())
    parts.append("")

    return "\n".join(parts)

# ── phase 1: planning ──────────────────────────────────────────────────────────

_PLANNING_PROMPT = """\
You are an automated code-improvement agent for a Discord news aggregator bot.

The bot polls Twitter RSS feeds and Telegram channels, deduplicates content
with embeddings, categorises with a local Ollama LLM (qwen2.5:7b), and posts
to Discord. All configuration lives in config.py; secrets live in .env.

The full codebase is below. Your job in this phase is to PLAN — do not write
code yet.

Analyse the codebase carefully and write a numbered list of the top 3-5
concrete improvements you intend to make. For each item:
  - State exactly which file(s) you will edit
  - Describe the specific change (be precise, not vague)
  - Explain the expected benefit

Focus areas (in priority order):
  1. Categorisation accuracy — the golden dataset sample shows ground truth.
     Look for categories the prompt handles poorly.
  2. Categories with F1=0 in tuning history (sports, music, fashion, fitness,
     software development) — are they underspecified in SYSTEM_PROMPT?
  3. Content filters — newsworthiness, engagement bait, short video: too tight
     or too loose?
  4. Code correctness — missing error handling at system boundaries (external
     APIs, user input), subtle bugs.
  5. Code quality — unnecessary complexity, missing docstrings on public APIs,
     dead code.

DO NOT output code in this phase. Output your analysis and numbered plan only.

---

{context_pack}
"""


def _call_with_retry(client, **kwargs):
    """Call client.messages.create with exponential backoff on 429 errors."""
    import anthropic
    delays = [15, 30, 60, 120]
    for attempt, delay in enumerate(delays, 1):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            if attempt == len(delays):
                raise
            print(f"Rate limit hit (attempt {attempt}); retrying in {delay}s … ({exc})", flush=True)
            time.sleep(delay)


def run_planning_phase(client, context_pack: str) -> str:
    print("Phase 1: Planning improvements …", flush=True)
    response = _call_with_retry(
        client,
        model=MODEL,
        max_tokens=MAX_TOKENS_PLAN,
        messages=[{"role": "user", "content": _PLANNING_PROMPT.format(context_pack=context_pack)}],
    )
    plan = "".join(b.text for b in response.content if hasattr(b, "text"))
    print(f"Plan produced ({len(plan):,} chars).", flush=True)
    return plan

# ── phase 2: implementation ────────────────────────────────────────────────────

_IMPLEMENTATION_PROMPT = """\
You are an automated code-improvement agent for a Discord news aggregator bot.

You wrote this improvement plan in the previous phase:

{plan}

Now implement it. For each planned change call `propose_file_edit` with:
  - file_path  : path relative to project root (e.g. "config.py")
  - old_content: the EXACT substring to replace, copied verbatim from the
                 source shown below. Must be unique within the file.
                 Leave empty only when creating a brand-new file.
  - new_content: the replacement text (or full content for new files)
  - rationale  : 1-2 sentences explaining the improvement

Hard constraints:
  - NEVER touch: .env, bot.log, data/, temp_media/, evals/runs/, evals/pending/,
    evals/variants/, evals/history.jsonl, or any *.session file
  - Do NOT remove any entry from VALID_CATEGORIES
  - Do NOT break the JSON output contract at the end of SYSTEM_PROMPT
  - DUPLICATE_THRESHOLD and SIMILARITY_THRESHOLD must remain in (0.0, 1.0)
  - NEWSWORTHINESS_THRESHOLD must remain in [1.0, 10.0]
  - Prefer targeted edits over full-file rewrites
  - Call `propose_file_edit` once per file

---

{context_pack}
"""


def run_implementation_phase(client, plan: str, context_pack: str) -> list[dict]:
    print("Phase 2: Implementing changes …", flush=True)
    response = _call_with_retry(
        client,
        model=MODEL,
        max_tokens=MAX_TOKENS_IMPL,
        tools=[EDIT_TOOL],
        messages=[{
            "role": "user",
            "content": _IMPLEMENTATION_PROMPT.format(plan=plan, context_pack=context_pack),
        }],
    )
    edits = [
        block.input
        for block in response.content
        if getattr(block, "type", None) == "tool_use" and block.name == "propose_file_edit"
    ]
    print(f"Received {len(edits)} file edit proposal(s).", flush=True)
    return edits

# ── apply edits ────────────────────────────────────────────────────────────────

def _is_blocked(file_path: str) -> bool:
    norm = file_path.replace("\\", "/")
    p = Path(file_path)
    if p.suffix in BLOCKED_EXTENSIONS:
        return True
    for prefix in BLOCKED_PREFIXES:
        if norm == prefix or norm.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


def apply_edit(edit: dict) -> tuple[bool, str]:
    """Apply one proposed edit. Returns (success, error_message)."""
    file_path: str = (edit.get("file_path") or "").strip()
    old_content: str = edit.get("old_content") or ""
    new_content: str = edit.get("new_content") or ""

    if not file_path:
        return False, "missing file_path"
    if not new_content:
        return False, "missing new_content"
    if _is_blocked(file_path):
        return False, f"blocked path: {file_path}"

    target = (PROJECT_ROOT / file_path).resolve()
    # Prevent path traversal outside the project
    try:
        target.relative_to(PROJECT_ROOT)
    except ValueError:
        return False, f"path escapes project root: {file_path}"

    target.parent.mkdir(parents=True, exist_ok=True)

    if old_content:
        if not target.exists():
            return False, f"file not found: {file_path}"
        original = target.read_text(encoding="utf-8")
        if old_content not in original:
            return False, f"old_content not found verbatim in {file_path}"
        patched = original.replace(old_content, new_content, 1)
        target.write_text(patched, encoding="utf-8")
    else:
        original = None
        target.write_text(new_content, encoding="utf-8")

    # Syntax-check Python files; revert on failure
    if target.suffix == ".py":
        try:
            ast.parse(target.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            if original is not None:
                target.write_text(original, encoding="utf-8")
            else:
                target.unlink(missing_ok=True)
            return False, f"syntax error in {file_path}: {exc}"

    return True, ""

# ── git helpers ────────────────────────────────────────────────────────────────

def _git(*args: str, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        check=check,
        text=True,
        capture_output=capture,
    )

# ── branch + PR creation ───────────────────────────────────────────────────────

def create_branch_and_pr(applied_edits: list[dict], plan: str) -> bool:
    slug = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"claude/auto-{slug}"
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    _git("config", "user.email", "claude-code@anthropic.com")
    _git("config", "user.name", "Claude Code")
    _git("checkout", "-b", branch)

    changed_files = [e["file_path"] for e in applied_edits]
    _git("add", "--", *changed_files)

    status = _git("status", "--porcelain", capture=True).stdout.strip()
    if not status:
        print("No staged changes after applying edits — nothing to commit.")
        _git("checkout", "main", check=False)
        _git("branch", "-D", branch, check=False)
        return False

    n = len(applied_edits)
    file_list = ", ".join(changed_files)
    commit_msg = (
        f"auto-improve: {n} file(s) updated\n\n"
        f"Files: {file_list}\n\n"
        f"Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
    )
    _git("commit", "-m", commit_msg)

    remote_url = f"https://x-access-token:{token}@github.com/{repo}.git"
    subprocess.run(
        ["git", "push", "-u", remote_url, branch],
        cwd=PROJECT_ROOT,
        check=True,
    )

    # Build PR body from per-file rationales + the plan
    rationale_lines = [
        f"- **`{e['file_path']}`**: {(e.get('rationale') or '').strip()}"
        for e in applied_edits
    ]
    pr_body = (
        "## What changed\n\n"
        + "\n".join(rationale_lines)
        + "\n\n## Improvement plan\n\n"
        + textwrap.indent(plan[:3000], "> ")
        + ("\n> …\n" if len(plan) > 3000 else "\n")
        + "\n---\n_Proposed by Claude Sonnet 4.6 via GitHub Actions._"
    )

    subprocess.run(
        [
            "gh", "pr", "create",
            "--title", f"auto-improve: {n} change(s) ({slug})",
            "--body", pr_body,
            "--base", "main",
            "--head", branch,
        ],
        cwd=PROJECT_ROOT,
        check=True,
        env={**os.environ, "GH_TOKEN": token},
    )
    print(f"PR created: branch={branch}", flush=True)
    return True

# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        import anthropic
    except ImportError:
        raise SystemExit("anthropic SDK not installed. Run: pip install anthropic")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set.")

    client = anthropic.Anthropic()

    print("Building codebase context pack …", flush=True)
    context_pack = build_context_pack()
    print(f"Context pack: {len(context_pack):,} chars", flush=True)

    plan = run_planning_phase(client, context_pack)
    print("\n─── PLAN ───────────────────────────────────")
    print(plan)
    print("─── END PLAN ───────────────────────────────\n")

    print(f"Waiting {PHASE_DELAY_SECS}s for rate-limit window to reset …", flush=True)
    time.sleep(PHASE_DELAY_SECS)

    edits = run_implementation_phase(client, plan, context_pack)
    if not edits:
        print("No file edits proposed — exiting without a PR.")
        return

    applied: list[dict] = []
    for edit in edits:
        ok, err = apply_edit(edit)
        fp = edit.get("file_path", "?")
        if ok:
            print(f"  ✓ {fp}")
            applied.append(edit)
        else:
            print(f"  ✗ {fp}: {err}", file=sys.stderr)

    if not applied:
        print("No edits applied successfully — exiting without a PR.")
        return

    create_branch_and_pr(applied, plan)


if __name__ == "__main__":
    main()
