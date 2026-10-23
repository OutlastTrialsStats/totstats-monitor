from __future__ import annotations

import contextlib
import sys
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
MAX_FILE = 2 * 1024 * 1024


@dataclass(frozen=True)
class LogRecord:
    seq: int
    ts: datetime
    level: str
    message: str

    def format(self) -> str:
        stamp = self.ts.strftime("%Y-%m-%d %H:%M:%S")
        if self.level == "INFO":
            return f"[{stamp}] {self.message}"
        return f"[{stamp}] {self.level} {self.message}"


class AppLog:
    def __init__(self, path: Path | None, echo: bool, level: str) -> None:
        self._lock = threading.Lock()
        self._buffer: deque[LogRecord] = deque(maxlen=1000)
        self._seq = 0
        self._path = path
        self._echo = echo
        self._level = LEVELS[level]
        if echo:
            for stream in (sys.stdout, sys.stderr):
                with contextlib.suppress(AttributeError, OSError, ValueError):
                    stream.reconfigure(encoding="utf-8", errors="replace")
        with contextlib.suppress(OSError):
            if path is not None and path.stat().st_size > MAX_FILE:
                path.unlink()

    def debug(self, message: str) -> None:
        self._write("DEBUG", message)

    def info(self, message: str) -> None:
        self._write("INFO", message)

    def warning(self, message: str) -> None:
        self._write("WARNING", message)

    def error(self, message: str) -> None:
        self._write("ERROR", message)

    def since(self, seq: int) -> list[LogRecord]:
        with self._lock:
            return [record for record in self._buffer if record.seq > seq]

    def _write(self, level: str, message: str) -> None:
        if LEVELS[level] < self._level:
            return
        with self._lock:
            self._seq += 1
            record = LogRecord(self._seq, datetime.now(), level, message)
            self._buffer.append(record)
            line = record.format() + "\n"
            if self._echo:
                with contextlib.suppress(AttributeError, OSError, ValueError):
                    sys.stdout.write(line)
                    sys.stdout.flush()
            if self._path is not None:
                try:
                    with open(self._path, "a", encoding="utf-8") as handle:
                        handle.write(line)
                except OSError:
                    self._path = None
