from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from totstats.shared.applog import AppLog

LINE_RE = re.compile(r"^\[(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2}):(\d{3})\]\[\s*\d+\]")
PRIMARY = "OPP.log"
MAX_REPLAY = 16 * 1024 * 1024
MAX_READ = 4 * 1024 * 1024
BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class LogLine:
    raw: str
    ts: datetime | None
    replay: bool


def parse_line(raw: str, replay: bool = False) -> LogLine:
    match = LINE_RE.match(raw)
    ts = None
    if match:
        year, month, day, hour, minute, second, milli = map(int, match.groups())
        with contextlib.suppress(ValueError):
            ts = datetime(year, month, day, hour, minute, second, milli * 1000)
    return LogLine(raw, ts, replay)


@dataclass(frozen=True)
class _Subscription:
    name: str
    sink: Callable[[LogLine], None]
    needles: tuple[str, ...]
    on_rotate: Callable[[], None] | None
    on_replay_complete: Callable[[], None] | None


class LogTailer:
    def __init__(self, logs_dir: Path, log: AppLog) -> None:
        self._logs_dir = logs_dir
        self._log = log
        self._subs: list[_Subscription] = []
        self._needles: tuple[str, ...] = ()
        self.reset()

    def subscribe(
        self,
        name: str,
        sink: Callable[[LogLine], None],
        needles: Sequence[str],
        on_rotate: Callable[[], None] | None = None,
        on_replay_complete: Callable[[], None] | None = None,
    ) -> None:
        self._subs.append(_Subscription(name, sink, tuple(needles), on_rotate, on_replay_complete))
        self._needles = tuple(dict.fromkeys(needle for sub in self._subs for needle in sub.needles))

    def reset(self) -> None:
        self.path: Path | None = None
        self.replaying = False
        self._identity: tuple[int, int] | None = None
        self._offset = 0
        self._pending = b""
        self._discard_partial = False
        self._replay_until = 0
        self._waiting = False

    def poll(self) -> None:
        try:
            self._poll()
        except OSError:
            pass
        except Exception as exc:
            self._log.warning(f"Log tailer failed: {exc}")

    def _poll(self) -> None:
        path = self._select_file()
        if path is None:
            if not self._waiting:
                self._log.info(f"Waiting for a game log in {self._logs_dir}")
                self._waiting = True
            return
        self._waiting = False

        stat = path.stat()
        identity = (stat.st_ino, stat.st_ctime_ns)
        if self.path != path or self._identity != identity or stat.st_size < self._offset:
            self._begin(path, identity, stat.st_size)
        self._read(path)

    def _select_file(self) -> Path | None:
        primary = self._logs_dir / PRIMARY
        try:
            if primary.is_file():
                return primary
            candidates = [p for p in self._logs_dir.glob("*.log") if p.is_file()]
            return max(candidates, key=lambda p: p.stat().st_mtime, default=None)
        except OSError:
            return None

    def _begin(self, path: Path, identity: tuple[int, int], size: int) -> None:
        rotated = self.path is not None
        self.path = path
        self._identity = identity
        self._pending = b""
        self._offset = max(0, size - MAX_REPLAY)
        self._discard_partial = self._offset > 0
        self._replay_until = size
        self.replaying = True

        if rotated:
            self._log.info(f"Log file rotated, following {path.name}")
            for sub in self._subs:
                if sub.on_rotate is not None:
                    self._call(sub, sub.on_rotate)
        else:
            self._log.info(f"Following log file {path.name}")

    def _read(self, path: Path) -> None:
        with open(path, "rb") as handle:
            handle.seek(self._offset)
            chunk = handle.read(MAX_READ)

        if not chunk:
            if self.replaying:
                self._finish_replay()
            return

        if self._offset == 0 and chunk.startswith(BOM):
            chunk = chunk[len(BOM) :]
            self._offset += len(BOM)
        self._offset += len(chunk)
        buffer = self._pending + chunk

        if self._discard_partial:
            _, found, buffer = buffer.partition(b"\n")
            if not found:
                self._pending = b""
                return
            self._discard_partial = False

        complete, newline, self._pending = buffer.rpartition(b"\n")
        if newline:
            for raw in complete.split(b"\n"):
                line = raw.rstrip(b"\r")
                if line:
                    self._dispatch(line.decode("utf-8", "replace"))

        if self.replaying and self._offset >= self._replay_until:
            self._finish_replay()

    def _finish_replay(self) -> None:
        self.replaying = False
        for sub in self._subs:
            if sub.on_replay_complete is not None:
                self._call(sub, sub.on_replay_complete)

    def _dispatch(self, raw: str) -> None:
        if not any(needle in raw for needle in self._needles):
            return
        line = parse_line(raw, self.replaying)
        for sub in self._subs:
            if any(needle in raw for needle in sub.needles):
                self._call(sub, sub.sink, line)

    def _call(self, sub: _Subscription, fn: Callable[..., None], *args: object) -> None:
        try:
            fn(*args)
        except Exception as exc:
            self._log.warning(f"Log subscriber {sub.name} failed: {exc}")
