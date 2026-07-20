from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
from typing import Callable, Mapping, Sequence

from .attempt_ledger import AttemptLedger
from .execution_policy import ExecutionProfile
from .git_ops import ProcessResult


_SECRET_NAME = re.compile(r"(?:token|secret|password|passwd|api[_-]?key|credential|authorization)", re.I)
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*(?:bearer\s+)?)([^\s\"']+)"),
    re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]+)"),
    re.compile(r"(?i)\b((?:api[_-]?key|token|password|secret)\s*[=:]\s*)([^\s,;\"']+)"),
)
_PROGRESS_EVENTS = {
    "thread.started",
    "turn.started",
    "turn.completed",
    "turn.failed",
    "item.started",
    "item.updated",
    "item.completed",
    "error",
}


class DiagnosticRedactionError(RuntimeError):
    pass


def redact_diagnostic(value: str, env: Mapping[str, str] | None = None) -> str:
    try:
        redacted = value
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        for name, secret in (env or {}).items():
            if _SECRET_NAME.search(name) and secret and len(secret) >= 4:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    except Exception as exc:  # fail closed: callers must not persist the payload
        raise DiagnosticRedactionError("diagnostic redaction failed") from exc


@dataclass(frozen=True)
class SupervisorPolicy:
    profile: ExecutionProfile = ExecutionProfile.CONTRACT
    heartbeat_seconds: float = 30.0
    slow_seconds: float = 60.0
    stalled_seconds: float = 120.0
    takeover_seconds: float = 180.0
    hard_timeout_seconds: float | None = None
    poll_seconds: float = 0.1
    terminate_grace_seconds: float = 2.0
    max_output_bytes: int = 256_000

    @property
    def effective_hard_timeout(self) -> float | None:
        if self.hard_timeout_seconds is not None:
            return self.hard_timeout_seconds
        if self.profile is ExecutionProfile.HIGH_RISK:
            return 900.0
        return None


@dataclass(frozen=True)
class ProgressDecision:
    events: tuple[str, ...]
    terminate_reason: str | None = None


class ProgressState:
    def __init__(self, started_at: float) -> None:
        self.last_progress_at = started_at
        self.last_diff_digest = ""
        self.last_heartbeat_at = started_at
        self.slow_emitted = False
        self.stalled_emitted = False
        self.failed_liveness_probes = 0

    def observe(
        self,
        *,
        now: float,
        output_progress: bool,
        diff_digest: str,
        liveness_ok: bool,
        policy: SupervisorPolicy,
    ) -> ProgressDecision:
        events: list[str] = []
        diff_progress = bool(diff_digest and diff_digest != self.last_diff_digest)
        if output_progress or diff_progress:
            self.last_progress_at = now
            self.slow_emitted = False
            self.stalled_emitted = False
            self.failed_liveness_probes = 0
        if diff_digest:
            self.last_diff_digest = diff_digest
        age = now - self.last_progress_at
        if now - self.last_heartbeat_at >= policy.heartbeat_seconds:
            self.last_heartbeat_at = now
            events.append("heartbeat")
        if age >= policy.slow_seconds and not self.slow_emitted:
            self.slow_emitted = True
            events.append("slow")
        if age >= policy.stalled_seconds and not self.stalled_emitted:
            self.stalled_emitted = True
            events.append("stalled_diagnostic")
        if age >= policy.stalled_seconds:
            self.failed_liveness_probes = 0 if liveness_ok else self.failed_liveness_probes + 1
        if (
            policy.profile is ExecutionProfile.DOCS_ONLY
            and age >= policy.takeover_seconds
            and self.failed_liveness_probes >= 2
        ):
            return ProgressDecision(tuple(events), "main_takeover_ready")
        return ProgressDecision(tuple(events))


@dataclass(frozen=True)
class SupervisedProcessResult:
    process: ProcessResult
    reason: str
    elapsed_seconds: float
    descendants_remaining: bool


class AttemptSupervisor:
    def __init__(
        self,
        *,
        ledger: AttemptLedger,
        attempt_id: str,
        unit_id: str,
        phase: str,
        policy: SupervisorPolicy,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        diff_probe: Callable[[], str] | None = None,
        liveness_probe: Callable[[int], bool] | None = None,
        cancel_probe: Callable[[], bool] | None = None,
    ) -> None:
        self.ledger = ledger
        self.attempt_id = attempt_id
        self.unit_id = unit_id
        self.phase = phase
        self.policy = policy
        self.clock = clock
        self.sleeper = sleeper
        self.diff_probe = diff_probe or (lambda: "")
        self.liveness_probe = liveness_probe or ProcessActivityProbe()
        self.cancel_probe = cancel_probe or (lambda: False)
        self._redaction_failed = False

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: str | Path,
        input_text: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> SupervisedProcessResult:
        started = self.clock()
        process = subprocess.Popen(
            list(args),
            cwd=Path(cwd),
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
            bufsize=1,
        )
        process_group = os.getpgid(process.pid)
        output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        writer_errors: queue.Queue[str] = queue.Queue()
        readers = [
            threading.Thread(target=self._read_stream, args=("stdout", process.stdout, output_queue), daemon=True),
            threading.Thread(target=self._read_stream, args=("stderr", process.stderr, output_queue), daemon=True),
        ]
        for reader in readers:
            reader.start()
        writer: threading.Thread | None = None
        if input_text is not None and process.stdin is not None:
            writer = threading.Thread(
                target=self._write_stdin,
                args=(process.stdin, input_text, writer_errors),
                daemon=True,
            )
            writer.start()

        stdout: deque[str] = deque()
        stderr: deque[str] = deque()
        stdout_size = stderr_size = 0
        progress = ProgressState(started)
        reason = "completed"
        self.ledger.update(
            {
                "attempt_id": self.attempt_id,
                "unit_id": self.unit_id,
                "phase": self.phase,
                "status": "running",
                "pid": process.pid,
                "process_group": process_group,
            }
        )
        try:
            while process.poll() is None:
                try:
                    output_progress, stdout_size, stderr_size = self._drain(
                        output_queue, stdout, stderr, stdout_size, stderr_size, env
                    )
                    now = self.clock()
                    if self.cancel_probe():
                        reason = "explicit_cancelled"
                        break
                    hard_timeout = self.policy.effective_hard_timeout
                    if hard_timeout is not None and now - started >= hard_timeout:
                        reason = "hard_timeout"
                        break
                    decision = progress.observe(
                        now=now,
                        output_progress=output_progress,
                        diff_digest=self.diff_probe(),
                        liveness_ok=self.liveness_probe(process.pid),
                        policy=self.policy,
                    )
                except Exception:
                    reason = "supervisor_probe_failed"
                    break
                for event_name in decision.events:
                    self._record_event(event_name, started, progress, process.pid, process_group)
                if decision.terminate_reason:
                    reason = decision.terminate_reason
                    break
                self.sleeper(self.policy.poll_seconds)
            if process.poll() is None:
                self._terminate_group(process, process_group)
            try:
                process.wait(timeout=max(1.0, self.policy.terminate_grace_seconds))
            except subprocess.TimeoutExpired:
                self._terminate_group(process, process_group)
                process.wait(timeout=max(1.0, self.policy.terminate_grace_seconds))
        finally:
            if process.poll() is None:
                self._terminate_group(process, process_group)
            elif self._group_alive(process_group):
                # The leader may exit while a grandchild still owns stdout or
                # continues mutating the worktree.  Every attempt owns the
                # entire process group, including this early-exit path.
                self._terminate_group(process, process_group)
            for reader in readers:
                reader.join(timeout=0.5)
            if writer is not None:
                writer.join(timeout=0.5)
            _, stdout_size, stderr_size = self._drain(
                output_queue, stdout, stderr, stdout_size, stderr_size, env
            )
        elapsed = self.clock() - started
        descendants_remaining = self._group_alive(process_group)
        if descendants_remaining:
            reason = "process_cleanup_failed"
        if not writer_errors.empty() and reason == "completed" and process.returncode == 0:
            reason = "stdin_write_failed"
        if process.returncode != 0 and reason == "completed":
            reason = "process_failure"
        status = "pass" if process.returncode == 0 and reason == "completed" else reason
        snapshot = self.ledger.update(
            {
                "status": status,
                "returncode": process.returncode,
                "elapsed_ms": int(elapsed * 1000),
                "descendants_remaining": descendants_remaining,
            }
        )
        if self._redaction_failed:
            self._record_event(
                "diagnostic_redaction_failed",
                started,
                progress,
                process.pid,
                process_group,
                revision=snapshot.revision,
            )
        self._record_event("process_closed", started, progress, process.pid, process_group, reason=reason, revision=snapshot.revision)
        return SupervisedProcessResult(
            ProcessResult(list(args), int(process.returncode or 0), "".join(stdout), "".join(stderr)),
            reason,
            elapsed,
            descendants_remaining,
        )

    def _drain(self, items, stdout, stderr, stdout_size, stderr_size, env):
        progress_seen = False
        while True:
            try:
                stream, value = items.get_nowait()
            except queue.Empty:
                break
            if stream == "stdout" and self._is_progress_line(value):
                progress_seen = True
            try:
                safe = redact_diagnostic(value, env)
            except DiagnosticRedactionError:
                self._redaction_failed = True
                continue
            target = stdout if stream == "stdout" else stderr
            size = stdout_size if stream == "stdout" else stderr_size
            target.append(safe)
            size += len(safe.encode("utf-8"))
            while target and size > self.policy.max_output_bytes:
                size -= len(target.popleft().encode("utf-8"))
            if stream == "stdout":
                stdout_size = size
            else:
                stderr_size = size
        return progress_seen, stdout_size, stderr_size

    @staticmethod
    def _read_stream(name, stream, output_queue) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            output_queue.put((name, line))
        stream.close()

    @staticmethod
    def _write_stdin(stream, value: str, errors: queue.Queue[str]) -> None:
        try:
            stream.write(value)
            stream.close()
        except (BrokenPipeError, OSError):
            errors.put("stdin_write_failed")
            try:
                stream.close()
            except OSError:
                pass

    @staticmethod
    def _is_progress_line(value: str) -> bool:
        if len(value.encode("utf-8", errors="replace")) > 65_536:
            return False
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return False
        if not isinstance(parsed, dict):
            return False
        event_type = parsed.get("type") or parsed.get("event")
        return isinstance(event_type, str) and event_type in _PROGRESS_EVENTS

    def _record_event(self, event, started, progress, pid, process_group, *, reason=None, revision=None):
        now = self.clock()
        elapsed_ms = max(0, int((now - started) * 1000))
        child_output_age_ms = max(0, int((now - progress.last_progress_at) * 1000))
        if revision is None and event in {"heartbeat", "slow", "stalled_diagnostic"}:
            revision = self.ledger.update(
                {
                    "last_event": event,
                    "heartbeat_elapsed_ms": elapsed_ms,
                    "child_output_age_ms": child_output_age_ms,
                }
            ).revision
        self.ledger.append_event(
            {
                "attempt_id": self.attempt_id,
                "unit_id": self.unit_id,
                "phase": self.phase,
                "event": event,
                "elapsed_ms": elapsed_ms,
                "child_output_age_ms": child_output_age_ms,
                "ledger_revision": revision if revision is not None else self.ledger.load().revision,
                "pid": pid,
                "process_group": process_group,
                **({"reason": reason} if reason else {}),
            }
        )

    def _terminate_group(self, process: subprocess.Popen, process_group: int) -> None:
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            return
        except PermissionError:
            process.terminate()
        deadline = time.monotonic() + self.policy.terminate_grace_seconds
        while time.monotonic() < deadline and self._group_alive(process_group):
            time.sleep(0.02)
        if self._group_alive(process_group):
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                process.kill()

    @staticmethod
    def _default_liveness(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    @staticmethod
    def _group_alive(process_group: int) -> bool:
        try:
            os.killpg(process_group, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


def prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class ProcessActivityProbe:
    """Treat CPU-time advancement as liveness independent of stdout/diff."""

    def __init__(self) -> None:
        self._last_cpu: dict[int, str] = {}

    def __call__(self, pid: int) -> bool:
        result = subprocess.run(
            ["ps", "-o", "time=", "-p", str(pid)],
            text=True,
            capture_output=True,
            check=False,
        )
        current = result.stdout.strip()
        if result.returncode != 0 or not current:
            return False
        previous = self._last_cpu.get(pid)
        self._last_cpu[pid] = current
        return previous is None or current != previous
