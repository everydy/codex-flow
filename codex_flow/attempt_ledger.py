from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import fcntl
import json
import os
import tempfile
import threading
from typing import Iterator, Mapping


class LedgerConflict(RuntimeError):
    pass


EVENT_FIELDS = {
    "attempt_id",
    "unit_id",
    "phase",
    "event",
    "elapsed_ms",
    "child_output_age_ms",
    "ledger_revision",
    "pid",
    "process_group",
    "reason",
}
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class LedgerSnapshot:
    revision: int
    record: dict


class AttemptLedger:
    """Small atomic ledger used as the source of truth for one attempt.

    JSON snapshots use revision CAS under an advisory lock.  The companion
    JSONL stream is diagnostic-only and may end in a partial record after a
    crash; readers deliberately ignore only that final incomplete line.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.events_path = self.path.with_name("events.jsonl")

    def load(self) -> LedgerSnapshot:
        if not self.path.exists():
            return LedgerSnapshot(0, {})
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return LedgerSnapshot(int(data.get("revision", 0)), dict(data.get("record") or {}))

    def compare_and_set(self, expected_revision: int, record: Mapping) -> LedgerSnapshot:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked():
            current = self.load()
            if current.revision != expected_revision:
                raise LedgerConflict(
                    f"attempt ledger revision changed: expected {expected_revision}, observed {current.revision}"
                )
            snapshot = LedgerSnapshot(expected_revision + 1, dict(record))
            self._atomic_write(
                {"revision": snapshot.revision, "record": snapshot.record}
            )
            return snapshot

    def update(self, patch: Mapping) -> LedgerSnapshot:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked():
            current = self.load()
            record = dict(current.record)
            record.update(dict(patch))
            snapshot = LedgerSnapshot(current.revision + 1, record)
            self._atomic_write(
                {"revision": snapshot.revision, "record": snapshot.record}
            )
            return snapshot

    def append_event(self, event: Mapping) -> dict:
        unknown = set(event) - EVENT_FIELDS
        if unknown:
            raise ValueError(f"attempt event contains non-allowlisted fields: {sorted(unknown)}")
        sanitized = {key: event[key] for key in EVENT_FIELDS if key in event}
        encoded = json.dumps(sanitized, sort_keys=True, separators=(",", ":")) + "\n"
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        return sanitized

    def recover_events(self) -> tuple[list[dict], bool]:
        if not self.events_path.exists():
            return [], False
        raw = self.events_path.read_bytes()
        lines = raw.splitlines(keepends=True)
        recovered: list[dict] = []
        truncated = False
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                if index == len(lines) - 1:
                    truncated = True
                    break
                raise
            if isinstance(value, dict):
                recovered.append(value)
        return recovered, truncated

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        key = str(self.lock_path.resolve())
        with _THREAD_LOCKS_GUARD:
            thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
        with thread_lock:
            with self.lock_path.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _atomic_write(self, value: Mapping) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, self.path)
        directory_fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
