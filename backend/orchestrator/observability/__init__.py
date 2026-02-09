"""
Observability module for the bounded agent.

Provides structured logging and tracing for:
- Full request lifecycle
- Tool calls and validation
- Performance metrics
- Debugging and evaluation
"""

from . import logger as trace
from .logger import AgentLogger, AgentRunLog, StepLog, ToolCallLog, get_runtime_logger

__all__ = [
    "AgentLogger",
    "AgentRunLog",
    "StepLog",
    "ToolCallLog",
    "get_runtime_logger",
    "trace",
]
