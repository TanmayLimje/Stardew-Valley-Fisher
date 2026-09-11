"""Structured logging for Fisher agent."""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional


class JsonlSessionLogger:
    """Appends structured JSON records for each episode or telemetry tick."""

    def __init__(self, log_dir: str | Path = "reports", prefix: str = "session"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.log_path = self.log_dir / f"{prefix}_{timestamp}.jsonl"
        self._file = open(self.log_path, "a", encoding="utf-8")

    def log_record(self, record_type: str, data: Dict[str, Any]) -> None:
        payload = {
            "timestamp": time.time(),
            "iso_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "type": record_type,
            **data,
        }
        self._file.write(json.dumps(payload) + "\n")
        self._file.flush()

    def close(self) -> None:
        if self._file and not self._file.closed:
            self._file.close()


def setup_logger(
    name: str = "fisher",
    level: int = logging.INFO,
    session_dir: Optional[str | Path] = None,
) -> tuple[logging.Logger, Optional[JsonlSessionLogger]]:
    """Configure console logger and optional JSONL session logger."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    jsonl_logger = None
    if session_dir is not None:
        jsonl_logger = JsonlSessionLogger(log_dir=session_dir)

    return logger, jsonl_logger
