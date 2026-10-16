"""Backfill saved conversation embeddings and wait for completion, or show counts.

Run with the orchestrator virtualenv. Uses backend/.env; no content is printed.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT.parent / ".env")

import conversation_index_jobs  # noqa: E402
import conversation_search  # noqa: E402
from db_migrations import run_pending_migrations  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--status", action="store_true", help="Read index counts without changing anything"
    )
    args = parser.parse_args()
    if args.status:
        print(conversation_index_jobs.get_worker_status())
        return
    run_pending_migrations()
    conversation_search.prepare_backfill()
    last_counts = None
    while True:
        counts = conversation_index_jobs.get_worker_status()
        if counts != last_counts:
            print(
                f"Messages: {counts['total']}; pending: {counts['pending']}; retrying: {counts['retrying']}",
                flush=True,
            )
            last_counts = counts
        if counts["pending"] == 0:
            return
        try:
            if conversation_search.process_due_once():
                continue
        except Exception:
            print("Indexing request failed; retry scheduled.", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
