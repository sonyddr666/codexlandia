from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Callable


class AppLogger:
    def __init__(self, log_file: Path, emit: Callable[[str], None] | None = None) -> None:
        self._log_file = Path(log_file)
        self._emit = emit
        self._lock = Lock()

    def set_log_file(self, log_file: Path) -> None:
        with self._lock:
            self._log_file = Path(log_file)

    def info(self, message: str) -> None:
        self.log("INFO", message)

    def warning(self, message: str) -> None:
        self.log("WARN", message)

    def error(self, message: str) -> None:
        self.log("ERROR", message)

    def log(self, level: str, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] [{level}] {message}"
        with self._lock:
            self._log_file.parent.mkdir(parents=True, exist_ok=True)
            with self._log_file.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        if self._emit is not None:
            self._emit(line)
