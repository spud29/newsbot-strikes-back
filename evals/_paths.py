"""Shared path constants for the eval harness.

Every eval script resolves paths through this module so the scripts can be
invoked from any working directory and still read/write the right files.
"""
import os
import sys

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(EVALS_DIR)

GOLDEN_PATH = os.path.join(EVALS_DIR, "categorization_golden.jsonl")
HISTORY_PATH = os.path.join(EVALS_DIR, "history.jsonl")
RUNS_DIR = os.path.join(EVALS_DIR, "runs")
VARIANTS_DIR = os.path.join(EVALS_DIR, "variants")
BASELINE_VARIANT_PATH = os.path.join(VARIANTS_DIR, "baseline.json")
PENDING_DIR = os.path.join(VARIANTS_DIR, "pending")
REPORTS_DIR = os.path.join(EVALS_DIR, "reports")
REPORTS_IMG_DIR = os.path.join(REPORTS_DIR, "img")
LOCK_PATH = os.path.join(EVALS_DIR, ".tuning.lock")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.py")


def ensure_project_on_syspath():
    """Put the project root on sys.path so `import config`, `import ollama_client` work."""
    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)
