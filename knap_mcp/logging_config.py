"""Structured logging to stderr.

stderr and never stdout: over stdio transport, stdout *is* the MCP channel, and a
stray log line there is a protocol error that reads to the client as a malformed
response.

Note paths never appear in a log line. On a hosted deployment the container log
is read by us, not by the customer whose vault it describes, and
``Clients/Acme/2026 renewal.md`` is a fact about someone's business. Log the tool
and the error type; where a path really is needed to debug, log ``path_hash``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from contextlib import contextmanager
from time import perf_counter
from typing import Iterator, Optional

_CONFIGURED = False


def path_hash(rel: str) -> str:
    """A stable, non-reversible id for a note path, for correlating log lines."""
    return hashlib.sha256(rel.encode("utf-8")).hexdigest()[:10]


class LoggingConfig:
    def setup(self, level: Optional[str] = None) -> None:
        global _CONFIGURED
        if _CONFIGURED:
            return
        resolved = (level or os.getenv("KNAP_MCP_LOG_LEVEL") or "INFO").upper()
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        root = logging.getLogger("knap_mcp")
        root.handlers.clear()
        root.addHandler(handler)
        root.setLevel(getattr(logging, resolved, logging.INFO))
        root.propagate = False
        _CONFIGURED = True


logging_config = LoggingConfig()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith("knap_mcp") else f"knap_mcp.{name}")


class PerfLogger:
    """Timing for the operations worth timing, at debug level."""

    def __init__(self) -> None:
        self._logger = get_logger("knap_mcp.perf")

    @contextmanager
    def track_operation(self, name: str) -> Iterator[None]:
        started = perf_counter()
        try:
            yield
        finally:
            self._logger.debug("%s took %.1fms", name, (perf_counter() - started) * 1000)


perf_logger = PerfLogger()
