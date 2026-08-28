#!/usr/bin/env python3
"""Report document files and database records that no longer match.

Run from backend/orchestrator:
    python scripts/find_document_orphans.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_conn
from documents import DOCUMENT_STORAGE_DIR


def find_document_orphans(storage_dir: Path = DOCUMENT_STORAGE_DIR) -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT document_id, file_path FROM documents ORDER BY document_id")
        rows = cur.fetchall()

    db_paths = {Path(str(row["file_path"])).resolve() for row in rows if row.get("file_path")}
    missing_db_files = [
        {"document_id": row["document_id"], "file_path": row.get("file_path")}
        for row in rows
        if not row.get("file_path") or not Path(str(row["file_path"])).exists()
    ]
    disk_files = sorted(
        path.resolve()
        for path in storage_dir.rglob("*")
        if path.is_file() and path.name != ".gitignore"
    )
    orphan_files = [str(path) for path in disk_files if path not in db_paths]
    return {
        "storage_dir": str(storage_dir),
        "orphan_files": orphan_files,
        "missing_db_files": missing_db_files,
        "orphan_file_count": len(orphan_files),
        "missing_db_file_count": len(missing_db_files),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-dir", type=Path, default=DOCUMENT_STORAGE_DIR)
    args = parser.parse_args()
    print(json.dumps(find_document_orphans(args.storage_dir), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
