from __future__ import annotations

import logging
import sys
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

LOG_LEVELS = {"debug", "info", "decision", "warning", "error"}
DECISION_LEVEL = 25
INTENTIONAL_DEBUG_LEVEL = 15
ORCHESTRATOR_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class LogEntry:
    entry_id: int
    timestamp: str
    level: str
    message: str
    context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.entry_id,
            "timestamp": self.timestamp,
            "level": self.level,
            "message": self.message,
            "context": self.context,
        }


class LogBuffer:
    def __init__(self, max_entries: int = 1000) -> None:
        self._entries: deque[LogEntry] = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._counter = 0

    def append(self, level: str, message: str, context: dict[str, Any] | None = None) -> LogEntry:
        normalized = (level or "info").lower()
        if normalized not in LOG_LEVELS:
            normalized = "info"
        timestamp = datetime.now(timezone.utc).isoformat()

        with self._lock:
            self._counter += 1
            entry = LogEntry(
                entry_id=self._counter,
                timestamp=timestamp,
                level=normalized,
                message=message,
                context=context,
            )
            self._entries.append(entry)
            return entry

    def get_since(self, last_id: int, level: str | None = None) -> list[LogEntry]:
        with self._lock:
            entries = [entry for entry in self._entries if entry.entry_id > last_id]
        if level:
            return [entry for entry in entries if entry.level == level]
        return entries

    def get_recent(
        self,
        since_minutes: int | None = None,
        level: str | None = None,
        limit: int | None = None,
    ) -> list[LogEntry]:
        cutoff = None
        if since_minutes is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

        with self._lock:
            entries = list(self._entries)

        if level:
            entries = [entry for entry in entries if entry.level == level]

        if cutoff:
            filtered: list[LogEntry] = []
            for entry in entries:
                try:
                    timestamp = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if timestamp >= cutoff:
                    filtered.append(entry)
            entries = filtered

        entries.sort(key=lambda entry: entry.entry_id)
        if limit is not None and limit > 0:
            entries = entries[-limit:]
        return entries


_buffer: LogBuffer | None = None
_buffer_lock = threading.Lock()
_stdout_installed = False
_logging_configured = False


def get_log_buffer() -> LogBuffer:
    global _buffer
    if _buffer is None:
        with _buffer_lock:
            if _buffer is None:
                _buffer = LogBuffer()
    return _buffer


def record_log(level: str, message: str, context: dict[str, Any] | None = None) -> None:
    if not message:
        return
    get_log_buffer().append(level, message, context=context)


def _init_decision_level() -> None:
    if logging.getLevelName(DECISION_LEVEL) == "Level 25":
        logging.addLevelName(DECISION_LEVEL, "DECISION")

        def decision(self: logging.Logger, message: str, *args: Any, **kwargs: Any) -> None:
            self.log(DECISION_LEVEL, message, *args, **kwargs)

        logging.Logger.decision = decision


def _init_intentional_debug_level() -> None:
    if logging.getLevelName(INTENTIONAL_DEBUG_LEVEL) == f"Level {INTENTIONAL_DEBUG_LEVEL}":
        logging.addLevelName(INTENTIONAL_DEBUG_LEVEL, "IDEBUG")

        def intentional_debug(
            self: logging.Logger, message: str, *args: Any, **kwargs: Any
        ) -> None:
            self.log(INTENTIONAL_DEBUG_LEVEL, message, *args, **kwargs)

        logging.Logger.intentional_debug = intentional_debug


class LogBufferHandler(logging.Handler):
    @staticmethod
    def _is_orchestrator_record(record: logging.LogRecord) -> bool:
        pathname = getattr(record, "pathname", "") or ""
        if not pathname:
            return False
        try:
            record_path = Path(pathname).resolve()
        except Exception:
            return False
        return ORCHESTRATOR_ROOT in record_path.parents or record_path == ORCHESTRATOR_ROOT

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Ignore generic DEBUG noise from libraries/frameworks.
            # Keep only orchestrator runtime debug (plus explicit intentional debug level).
            if (
                record.levelno < logging.INFO
                and record.levelno != INTENTIONAL_DEBUG_LEVEL
                and not self._is_orchestrator_record(record)
            ):
                return
            message = self.format(record)
            level = self._map_level(record.levelno)
            context = {
                "logger": record.name,
                "module": record.module,
                "line": record.lineno,
            }
            record_log(level, message, context=context)
        except Exception:
            pass

    @staticmethod
    def _map_level(levelno: int) -> str:
        if levelno >= logging.ERROR:
            return "error"
        if levelno >= logging.WARNING:
            return "warning"
        if levelno == DECISION_LEVEL:
            return "decision"
        if levelno == INTENTIONAL_DEBUG_LEVEL:
            return "debug"
        if levelno >= logging.INFO:
            return "info"
        return "debug"


def configure_logging(level: str | None = None) -> None:
    global _logging_configured
    if _logging_configured:
        return

    _init_decision_level()
    _init_intentional_debug_level()
    root = logging.getLogger()
    # Keep root at DEBUG so the in-memory stream captures all levels,
    # including debug records. Individual handlers still control what is
    # emitted to their own outputs.
    root.setLevel(logging.DEBUG)

    handler = LogBufferHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)

    _logging_configured = True


def _classify_level(line: str, default_level: str) -> str:
    lowered = line.lower()
    if "error" in lowered or "✗" in line:
        return "error"
    if "warning" in lowered or "warn" in lowered or "⚠" in line:
        return "warning"
    if "debug" in lowered:
        return "debug"
    if "decision" in lowered:
        return "decision"
    return default_level


class _StreamProxy:
    def __init__(self, original: Any, default_level: str) -> None:
        self._original = original
        self._default_level = default_level
        self._buffer = ""

    def write(self, text: str) -> int:
        if text:
            self._original.write(text)
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                line = line.rstrip("\r")
                if line.strip():
                    level = _classify_level(line, self._default_level)
                    record_log(level, line)
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            level = _classify_level(self._buffer, self._default_level)
            record_log(level, self._buffer.strip())
        self._buffer = ""
        self._original.flush()

    def isatty(self) -> bool:
        return bool(getattr(self._original, "isatty", lambda: False)())

    def fileno(self) -> int:
        return int(getattr(self._original, "fileno", lambda: 0)())


def install_stdout_logger() -> None:
    global _stdout_installed
    if _stdout_installed:
        return
    _stdout_installed = True

    sys.stdout = _StreamProxy(sys.stdout, "info")
    sys.stderr = _StreamProxy(sys.stderr, "error")
