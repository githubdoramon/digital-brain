"""Logging utilities for robot-gateway."""

import logging
import sys


def get_runtime_logger(name: str) -> logging.Logger:
    """Return a module logger."""
    return logging.getLogger(name)


def configure_logging() -> None:
    """Set up structured logging to stdout."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
