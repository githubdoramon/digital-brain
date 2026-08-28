from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from db import get_conn
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "db_migrations"
_MIGRATION_FILENAME_RE = re.compile(r"^(\d+)_([a-z0-9_]+)\.sql$")
_ADVISORY_LOCK_KEY = 732141910


@dataclass(frozen=True)
class MigrationFile:
    version: str
    filename: str
    path: Path
    checksum: str


def run_pending_migrations() -> list[str]:
    if not _env_flag("DB_AUTO_MIGRATE", default=True):
        logger.info("[db_migrations] Auto-migrate disabled via DB_AUTO_MIGRATE")
        return []

    migration_files = _load_migration_files(_MIGRATIONS_DIR)
    if not migration_files:
        logger.info("[db_migrations] No migration files found")
        return []

    applied_now: list[str] = []

    with get_conn() as conn, conn.cursor() as cur:
        _ensure_migration_table(cur)
        conn.commit()

        cur.execute("SELECT pg_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
        try:
            cur.execute("SELECT version, filename, checksum FROM schema_migrations")
            applied_rows = cur.fetchall()
            applied = {
                str(row.get("version") or ""): (
                    str(row.get("filename") or ""),
                    str(row.get("checksum") or ""),
                )
                for row in applied_rows
            }

            for migration in migration_files:
                applied_filename, existing_checksum = applied.get(
                    migration.version, ("", "")
                )
                if existing_checksum:
                    if existing_checksum != migration.checksum:
                        raise RuntimeError(
                            "Applied migration checksum mismatch for "
                            f"{migration.filename} (version={migration.version}; "
                            f"database_filename={applied_filename or 'unknown'})"
                        )
                    continue

                sql_text = migration.path.read_text(encoding="utf-8").strip()
                if not sql_text:
                    logger.info("[db_migrations] Skipping empty migration %s", migration.filename)
                    _record_applied_migration(cur, migration)
                    conn.commit()
                    applied_now.append(migration.filename)
                    continue

                logger.info("[db_migrations] Applying migration %s", migration.filename)
                cur.execute(sql_text)
                _record_applied_migration(cur, migration)
                conn.commit()
                applied_now.append(migration.filename)
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))

    if applied_now:
        logger.info("[db_migrations] Applied %d migration(s)", len(applied_now))
    else:
        logger.info("[db_migrations] No pending migrations")
    return applied_now


def _load_migration_files(directory: Path) -> list[MigrationFile]:
    if not directory.exists() or not directory.is_dir():
        return []

    migrations: list[MigrationFile] = []
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_FILENAME_RE.match(path.name)
        if not match:
            logger.warning("[db_migrations] Ignoring invalid migration filename %s", path.name)
            continue
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        migrations.append(
            MigrationFile(
                version=match.group(1),
                filename=path.name,
                path=path,
                checksum=checksum,
            )
        )

    migrations.sort(key=lambda item: (int(item.version), item.filename))
    versions: dict[str, list[str]] = {}
    for migration in migrations:
        versions.setdefault(migration.version, []).append(migration.filename)
    duplicates = {version: filenames for version, filenames in versions.items() if len(filenames) > 1}
    if duplicates:
        details = "; ".join(
            f"{version}: {', '.join(filenames)}"
            for version, filenames in sorted(duplicates.items(), key=lambda item: int(item[0]))
        )
        raise RuntimeError(f"Duplicate migration version(s): {details}")
    return migrations


def _ensure_migration_table(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _record_applied_migration(cur, migration: MigrationFile) -> None:
    cur.execute(
        """
        INSERT INTO schema_migrations (version, filename, checksum)
        VALUES (%s, %s, %s)
        """,
        (migration.version, migration.filename, migration.checksum),
    )


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default
