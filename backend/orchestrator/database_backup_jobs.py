from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import async_jobs
from observability.logger import get_runtime_logger
from scheduled_jobs import DATABASE_BACKUP

logger = get_runtime_logger(__name__)

JOB_TYPE = DATABASE_BACKUP.job_type
POLL_SECONDS = DATABASE_BACKUP.poll_seconds
RETRY_SECONDS = DATABASE_BACKUP.retry_seconds or 300
RUN_HOUR = DATABASE_BACKUP.time_local.hour if DATABASE_BACKUP.time_local else 0
RUN_MINUTE = DATABASE_BACKUP.time_local.minute if DATABASE_BACKUP.time_local else 0
TIMEZONE_NAME = DATABASE_BACKUP.timezone_name or "Europe/Lisbon"
SYSTEM_USER_EMAIL = "system"

_WORKER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()


def start_worker() -> None:
    global _WORKER_THREAD
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _WORKER_THREAD = threading.Thread(
        target=_worker_loop,
        name="database-backup-jobs",
        daemon=True,
    )
    _WORKER_THREAD.start()
    logger.info("[database_backup.job] worker started timezone=%s", TIMEZONE_NAME)


def stop_worker(timeout: float = 5.0) -> None:
    _STOP_EVENT.set()
    thread = _WORKER_THREAD
    if thread and thread.is_alive():
        thread.join(timeout=timeout)


def enqueue_due_job(*, now_utc: datetime | None = None) -> bool:
    if _configuration_missing():
        return False

    resolved_now = _as_utc(now_utc or datetime.now(timezone.utc))
    local_now = resolved_now.astimezone(ZoneInfo(TIMEZONE_NAME))
    if (local_now.hour, local_now.minute) < (RUN_HOUR, RUN_MINUTE):
        return False

    run_date = local_now.date().isoformat()
    dedupe_key = f"{run_date}::{TIMEZONE_NAME}"
    existing = async_jobs.get_job(
        job_type=JOB_TYPE,
        user_email=SYSTEM_USER_EMAIL,
        dedupe_key=dedupe_key,
    )
    if existing:
        return False

    queued = async_jobs.enqueue_job(
        job_type=JOB_TYPE,
        user_email=SYSTEM_USER_EMAIL,
        dedupe_key=dedupe_key,
        payload={"date": run_date, "timezone": TIMEZONE_NAME},
        status_message="Queued for encrypted database backup",
    )
    if queued.get("created"):
        logger.info(
            "[database_backup.job] queued job_id=%s date=%s timezone=%s",
            queued.get("job_id"),
            run_date,
            TIMEZONE_NAME,
        )
        return True
    return False


def process_due_once() -> bool:
    job = async_jobs.claim_due_job(job_type=JOB_TYPE)
    if not job:
        return False

    job_id = str(job["job_id"])
    revision = int(job["revision"])
    try:
        result = create_and_upload_backup()
        async_jobs.mark_succeeded(
            job_id,
            result=result,
            status_message="Encrypted database backup uploaded",
            revision=revision,
        )
        logger.info(
            "[database_backup.job] completed job_id=%s key=%s bytes=%s",
            job_id,
            result.get("key"),
            result.get("size_bytes"),
        )
    except Exception as exc:
        async_jobs.mark_failed(
            job_id,
            error=str(exc),
            status_message="Encrypted database backup failed",
            revision=revision,
            retry_delay_seconds=RETRY_SECONDS,
        )
        logger.exception("[database_backup.job] failed job_id=%s", job_id)
    return True


def create_and_upload_backup() -> dict[str, Any]:
    passphrase = _required_env("DB_BACKUP_PASSPHRASE")
    bucket = _required_env("DB_BACKUP_S3_BUCKET")
    database_password = _required_env("POSTGRES_PASSWORD")
    database_user = os.getenv("DB_BACKUP_POSTGRES_USER") or _required_env("POSTGRES_USER")
    database_name = os.getenv("DB_BACKUP_POSTGRES_DB") or _required_env("POSTGRES_DB")
    database_host = os.getenv("POSTGRES_HOST", "db")
    database_port = os.getenv("POSTGRES_PORT", "5432")
    prefix = os.getenv("DB_BACKUP_S3_PREFIX", "").strip("/")
    region = (
        os.getenv("DB_BACKUP_AWS_REGION")
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or None
    )

    filename = "backup.sql.gz.enc"
    key = f"{prefix}/{filename}" if prefix else filename
    with tempfile.TemporaryDirectory(prefix="digital-brain-backup-") as temp_dir:
        encrypted_path = Path(temp_dir) / filename
        _write_encrypted_dump(
            encrypted_path=encrypted_path,
            database_host=database_host,
            database_port=database_port,
            database_user=database_user,
            database_name=database_name,
            database_password=database_password,
            passphrase=passphrase,
        )
        size_bytes = encrypted_path.stat().st_size
        _upload_to_s3(
            encrypted_path=encrypted_path,
            bucket=bucket,
            key=key,
            region=region,
        )

    return {"key": key, "size_bytes": size_bytes}


def _write_encrypted_dump(
    *,
    encrypted_path: Path,
    database_host: str,
    database_port: str,
    database_user: str,
    database_name: str,
    database_password: str,
    passphrase: str,
) -> None:
    pg_dump_env = os.environ.copy()
    pg_dump_env.pop("DB_BACKUP_PASSPHRASE", None)
    pg_dump_env["PGPASSWORD"] = database_password
    command_path = os.getenv("PATH", "/usr/bin:/bin")
    gzip_env = {"PATH": command_path}
    openssl_env = {"PATH": command_path, "DB_BACKUP_PASSPHRASE": passphrase}
    with (
        tempfile.TemporaryFile() as pg_dump_stderr,
        tempfile.TemporaryFile() as gzip_stderr,
        tempfile.TemporaryFile() as openssl_stderr,
        encrypted_path.open("wb") as encrypted_file,
    ):
        pg_dump = subprocess.Popen(
            [
                "pg_dump",
                "--no-password",
                "--host",
                database_host,
                "--port",
                database_port,
                "--username",
                database_user,
                "--format",
                "custom",
                database_name,
            ],
            stdout=subprocess.PIPE,
            stderr=pg_dump_stderr,
            env=pg_dump_env,
        )
        if pg_dump.stdout is None:
            raise RuntimeError("Could not open pg_dump output stream")
        gzip_process = subprocess.Popen(
            ["gzip", "-c"],
            stdin=pg_dump.stdout,
            stdout=subprocess.PIPE,
            stderr=gzip_stderr,
            env=gzip_env,
        )
        pg_dump.stdout.close()
        if gzip_process.stdout is None:
            raise RuntimeError("Could not open gzip output stream")
        openssl = subprocess.Popen(
            [
                "openssl",
                "enc",
                "-aes-256-cbc",
                "-pbkdf2",
                "-salt",
                "-pass",
                "env:DB_BACKUP_PASSPHRASE",
            ],
            stdin=gzip_process.stdout,
            stdout=encrypted_file,
            stderr=openssl_stderr,
            env=openssl_env,
        )
        gzip_process.stdout.close()
        openssl_status = openssl.wait()
        gzip_status = gzip_process.wait()
        pg_dump_status = pg_dump.wait()

        failures = []
        for name, status, stream in (
            ("pg_dump", pg_dump_status, pg_dump_stderr),
            ("gzip", gzip_status, gzip_stderr),
            ("openssl", openssl_status, openssl_stderr),
        ):
            if status != 0:
                stream.seek(0)
                detail = stream.read(4000).decode("utf-8", errors="replace").strip()
                failures.append(f"{name} exited {status}: {detail or 'no error output'}")
        if failures:
            raise RuntimeError("; ".join(failures))


def _upload_to_s3(*, encrypted_path: Path, bucket: str, key: str, region: str | None) -> None:
    import boto3

    client = boto3.client("s3", region_name=region) if region else boto3.client("s3")
    client.upload_file(
        str(encrypted_path),
        bucket,
        key,
        ExtraArgs={"ContentType": "application/octet-stream"},
    )


def get_worker_status() -> dict[str, Any]:
    missing_configuration = _configuration_missing()
    return {
        "job_type": JOB_TYPE,
        "worker_alive": bool(_WORKER_THREAD and _WORKER_THREAD.is_alive()),
        "poll_seconds": POLL_SECONDS,
        "retry_seconds": RETRY_SECONDS,
        "time_local": f"{RUN_HOUR:02d}:{RUN_MINUTE:02d}",
        "timezone_name": TIMEZONE_NAME,
        "configuration_ready": not missing_configuration,
        "missing_configuration": missing_configuration,
    }


def _worker_loop() -> None:
    while not _STOP_EVENT.is_set():
        try:
            enqueue_due_job()
            processed = process_due_once()
        except Exception:
            logger.exception("[database_backup.job] worker tick failed")
            processed = False
        if processed:
            time.sleep(0)
        else:
            _STOP_EVENT.wait(POLL_SECONDS)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def _configuration_missing() -> list[str]:
    return [
        name
        for name in ("DB_BACKUP_PASSPHRASE", "DB_BACKUP_S3_BUCKET", "POSTGRES_PASSWORD")
        if not os.getenv(name, "").strip()
    ]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
