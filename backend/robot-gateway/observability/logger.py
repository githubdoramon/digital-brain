"""Logging utilities for robot-gateway."""

import logging

from .log_stream import configure_logging as _configure_logging


def get_runtime_logger(name: str) -> logging.Logger:
    """Return a module logger."""
    return logging.getLogger(name)


def configure_logging() -> None:
    """Set up logging: in-memory buffer for /system/logs plus stderr console output."""
    _configure_logging()
