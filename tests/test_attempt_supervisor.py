from __future__ import annotations

from pathlib import Path
import os
import signal
import sys
import time

import codex_flow.attempt_supervisor as supervisor_module
import pytest

from codex_flow.attempt_ledger import AttemptLedger
from codex_flow.attempt_supervisor import (
    AttemptSupervisor,
    ProgressState,
    SupervisorPolicy,
    redact_diagnostic,
)
from codex_flow.execution_policy import ExecutionProfile


def test_progress_state_emits_30_60_120_boundaries():
    policy = SupervisorPolicy(profile=ExecutionProfile.CONTRACT)
    progress = ProgressState(0)

    assert progress.observe(now=30, output_progress=False, diff_digest="", liveness_ok=True, policy=policy).events == ("heartbeat",)
    assert progress.observe(now=60, output_progress=False, diff_digest="", liveness_ok=True, policy=policy).events == ("heartbeat", "slow")
    assert progress.observe(now=120, output_progress=False, diff_digest="", liveness_ok=True, policy=policy).events == ("heartbeat", "stalled_diagnostic")


def test_progress_or_diff_growth_resets_stall_clock():
    policy = SupervisorPolicy(profile=ExecutionProfile.CONTRACT)
    progress = ProgressState(0)
    progress.observe(now=120, output_progress=False, diff_digest="", liveness_ok=True, policy=policy)

    decision = progress.observe(now=121, output_progress=False, diff_digest="new", liveness_ok=True, policy=policy)

    assert "slow" not in decision.events
    assert progress.last_progress_at == 121


def test_docs_takeover_requires_two_failed_liveness_probes():
    policy = SupervisorPolicy(profile=ExecutionProfile.DOCS_ONLY)
    progress = ProgressState(0)

    first = progress.observe(now=180, output_progress=False, diff_digest="", liveness_ok=False, policy=policy)
    second = progress.observe(now=181, output_progress=False, diff_digest="", liveness_ok=False, policy=policy)

    assert first.terminate_reason is None
    assert second.terminate_reason == "main_takeover_ready"


def test_docs_takeover_is_reachable_with_production_activity_probe(tmp_path):
    ledger = AttemptLedger(tmp_path / "ledger.json")
    supervisor = AttemptSupervisor(
        ledger=ledger,
        attempt_id="attempt-docs",
        unit_id="unit-docs",
        phase="test",
        policy=SupervisorPolicy(
            profile=ExecutionProfile.DOCS_ONLY,
            heartbeat_seconds=0.02,
            slow_seconds=0.03,
            stalled_seconds=0.04,
            takeover_seconds=0.07,
            poll_seconds=0.02,
            terminate_grace_seconds=0.03,
        ),
    )

    result = supervisor.run([sys.executable, "-c", "import time; time.sleep(10)"], cwd=tmp_path)

    assert result.reason == "main_takeover_ready"
    assert result.process.status != 0


def test_high_risk_defaults_to_900_second_hard_cap():
    assert SupervisorPolicy(profile=ExecutionProfile.HIGH_RISK).effective_hard_timeout == 900
    assert SupervisorPolicy(profile=ExecutionProfile.CONTRACT).effective_hard_timeout is None


def test_supervisor_streams_bounded_redacted_output(tmp_path):
    ledger = AttemptLedger(tmp_path / "ledger.json")
    supervisor = AttemptSupervisor(
        ledger=ledger,
        attempt_id="attempt-1",
        unit_id="unit-1",
        phase="test",
        policy=SupervisorPolicy(max_output_bytes=80),
    )
    script = "import sys; print('token=very-secret-token'); print('x'*200, file=sys.stderr)"

    result = supervisor.run([sys.executable, "-c", script], cwd=tmp_path)

    assert result.process.status == 0
    assert "very-secret-token" not in result.process.stdout
    assert "[REDACTED]" in result.process.stdout
    assert len(result.process.stderr.encode()) <= 80
    assert ledger.load().record["status"] == "pass"
    assert ledger.load().record["descendants_remaining"] is False


def test_nonzero_exit_is_recorded_as_process_failure(tmp_path):
    ledger = AttemptLedger(tmp_path / "ledger.json")
    supervisor = AttemptSupervisor(
        ledger=ledger,
        attempt_id="attempt-fail",
        unit_id="unit-fail",
        phase="test",
        policy=SupervisorPolicy(),
    )

    result = supervisor.run([sys.executable, "-c", "raise SystemExit(7)"], cwd=tmp_path)

    assert result.reason == "process_failure"
    assert ledger.load().record["status"] == "process_failure"


@pytest.mark.parametrize("probe_name", ["cancel", "diff", "liveness"])
def test_probe_exception_finalizes_ledger_and_process_evidence(tmp_path, probe_name):
    def fail(*_args):
        raise RuntimeError("probe failed")

    ledger = AttemptLedger(tmp_path / f"{probe_name}-ledger.json")
    kwargs = {
        "cancel_probe": fail if probe_name == "cancel" else None,
        "diff_probe": fail if probe_name == "diff" else None,
        "liveness_probe": fail if probe_name == "liveness" else None,
    }
    supervisor = AttemptSupervisor(
        ledger=ledger,
        attempt_id=f"attempt-{probe_name}",
        unit_id="unit-probe",
        phase="test",
        policy=SupervisorPolicy(terminate_grace_seconds=0.03),
        **kwargs,
    )

    result = supervisor.run([sys.executable, "-c", "import time; time.sleep(10)"], cwd=tmp_path)
    events, _ = ledger.recover_events()

    assert result.reason == "supervisor_probe_failed"
    assert ledger.load().record["status"] == "supervisor_probe_failed"
    assert any(event["event"] == "process_closed" for event in events)


def test_supervisor_hard_timeout_closes_process(tmp_path):
    ledger = AttemptLedger(tmp_path / "ledger.json")
    supervisor = AttemptSupervisor(
        ledger=ledger,
        attempt_id="attempt-2",
        unit_id="unit-2",
        phase="test",
        policy=SupervisorPolicy(hard_timeout_seconds=0.15, poll_seconds=0.01, terminate_grace_seconds=0.05),
    )

    result = supervisor.run([sys.executable, "-c", "import time; time.sleep(10)"], cwd=tmp_path)

    assert result.reason == "hard_timeout"
    assert result.process.status != 0
    assert result.descendants_remaining is False


def test_heartbeat_updates_atomic_ledger_revision(tmp_path):
    ledger = AttemptLedger(tmp_path / "ledger.json")
    supervisor = AttemptSupervisor(
        ledger=ledger,
        attempt_id="attempt-heartbeat",
        unit_id="unit-heartbeat",
        phase="test",
        policy=SupervisorPolicy(
            heartbeat_seconds=0.02,
            slow_seconds=1,
            stalled_seconds=2,
            hard_timeout_seconds=0.08,
            poll_seconds=0.01,
            terminate_grace_seconds=0.03,
        ),
    )

    supervisor.run([sys.executable, "-c", "import time; time.sleep(10)"], cwd=tmp_path)
    events, _ = ledger.recover_events()

    heartbeat = next(event for event in events if event["event"] == "heartbeat")
    assert heartbeat["ledger_revision"] >= 2
    assert ledger.load().record["heartbeat_elapsed_ms"] >= 0


def test_large_stdin_to_nonreading_child_is_still_supervised(tmp_path):
    ledger = AttemptLedger(tmp_path / "ledger.json")
    supervisor = AttemptSupervisor(
        ledger=ledger,
        attempt_id="attempt-stdin",
        unit_id="unit-stdin",
        phase="test",
        policy=SupervisorPolicy(hard_timeout_seconds=0.08, poll_seconds=0.01, terminate_grace_seconds=0.05),
    )
    started = time.monotonic()

    result = supervisor.run(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        cwd=tmp_path,
        input_text="x" * 10_000_000,
    )

    assert time.monotonic() - started < 1.0
    assert result.reason == "hard_timeout"
    assert ledger.load().revision > 0


def test_explicit_cancel_wins_timeout_race_and_closes_process(tmp_path):
    ledger = AttemptLedger(tmp_path / "ledger.json")
    supervisor = AttemptSupervisor(
        ledger=ledger,
        attempt_id="attempt-cancel",
        unit_id="unit-cancel",
        phase="test",
        policy=SupervisorPolicy(hard_timeout_seconds=0.01, poll_seconds=0.01, terminate_grace_seconds=0.03),
        cancel_probe=lambda: True,
    )

    result = supervisor.run([sys.executable, "-c", "import time; time.sleep(10)"], cwd=tmp_path)

    assert result.reason == "explicit_cancelled"
    assert result.descendants_remaining is False


def test_parent_early_exit_does_not_leave_grandchild(tmp_path):
    ledger = AttemptLedger(tmp_path / "ledger.json")
    supervisor = AttemptSupervisor(
        ledger=ledger,
        attempt_id="attempt-tree",
        unit_id="unit-tree",
        phase="test",
        policy=SupervisorPolicy(terminate_grace_seconds=0.05),
    )
    script = (
        "import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)']); "
        "print(p.pid, flush=True)"
    )

    result = supervisor.run([sys.executable, "-c", script], cwd=tmp_path)
    grandchild_pid = int(result.process.stdout.strip())
    time.sleep(0.05)

    with pytest_raises_process_missing(grandchild_pid):
        os.kill(grandchild_pid, 0)


def test_redactor_covers_headers_and_secret_environment_values():
    value = "Authorization: Bearer abcdef token=ghijkl env-secret-value"

    redacted = redact_diagnostic(value, {"SERVICE_SECRET": "env-secret-value"})

    assert "abcdef" not in redacted
    assert "ghijkl" not in redacted
    assert "env-secret-value" not in redacted
    assert redacted.count("[REDACTED]") >= 3


def test_malformed_noise_and_oversized_json_are_not_progress():
    assert AttemptSupervisor._is_progress_line("plain noise\n") is False
    assert AttemptSupervisor._is_progress_line('{"unknown":"value"}\n') is False
    assert AttemptSupervisor._is_progress_line('{"event":"turn.started"}\n') is True
    assert AttemptSupervisor._is_progress_line('{"event":"' + ("x" * 70_000) + '"}') is False


def test_redactor_failure_drops_payload_and_records_correlated_event(tmp_path, monkeypatch):
    ledger = AttemptLedger(tmp_path / "ledger.json")
    supervisor = AttemptSupervisor(
        ledger=ledger,
        attempt_id="attempt-redactor",
        unit_id="unit-redactor",
        phase="test",
        policy=SupervisorPolicy(),
    )

    def fail_redaction(value, env=None):
        raise supervisor_module.DiagnosticRedactionError("boom")

    monkeypatch.setattr(supervisor_module, "redact_diagnostic", fail_redaction)
    result = supervisor.run([sys.executable, "-c", "print('raw-secret')"], cwd=tmp_path)
    events, _ = ledger.recover_events()

    assert result.process.stdout == ""
    failure = next(event for event in events if event["event"] == "diagnostic_redaction_failed")
    assert failure["pid"] > 0
    assert failure["process_group"] > 0


class pytest_raises_process_missing:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is ProcessLookupError:
            return True
        if exc_type is None:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            raise AssertionError(f"grandchild process {self.pid} remained alive")
        return False
