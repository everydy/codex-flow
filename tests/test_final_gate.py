import json
import subprocess

import pytest

from codex_flow import plans, tickets
from codex_flow.execution_policy import ExecutionMode, ExecutionProfile
from codex_flow.final_gate import (
    FinalGateError,
    FinalGateRecord,
    FinalizeGuard,
    plan_file_digest,
    produce_final_gate,
    require_final_gate,
    terminal_queue_revision,
    write_final_gate,
)
from codex_flow.merge import MergeRunner


def record(**overrides):
    values = {
        "reviewed_head": "abc",
        "plan_digest": "plan",
        "terminal_queue_revision": "queue",
        "cumulative_review_status": "pass",
        "canonical_test_status": "pass",
        "evidence_hash": "evidence",
    }
    values.update(overrides)
    return FinalGateRecord(**values)


def init_repo(path):
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "switch", "-c", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "codex-flow@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Codex Flow"], cwd=path, check=True)
    (path / "README.md").write_text("# Test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)


def terminal_plan(path):
    ticket = tickets.submit_ticket("Final gate", repo=path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=path)
    (plan.directory / "log.md").write_text(
        "# Log\n\n- Completed commit unit 1.\n- Completed commit unit 2.\n",
        encoding="utf-8",
    )
    plans.load_queue(plan.plan_path)
    return plan


def test_docs_interactive_is_direct_but_unattended_is_isolated():
    assert select_mode(ExecutionProfile.DOCS_ONLY, interactive=True) is ExecutionMode.PARENT_DIRECT
    assert select_mode(ExecutionProfile.DOCS_ONLY, interactive=False) is ExecutionMode.ISOLATED_CHILD
    assert select_mode(ExecutionProfile.HIGH_RISK, interactive=True) is ExecutionMode.ISOLATED_CHILD


def select_mode(profile, *, interactive):
    from codex_flow.final_gate import select_mode as actual

    return actual(profile, interactive=interactive)


def test_final_gate_requires_fresh_head_plan_queue_and_both_passes(tmp_path):
    path = tmp_path / "gate.json"
    write_final_gate(path, record())
    assert require_final_gate(
        path,
        expected_head="abc",
        expected_plan_digest="plan",
        expected_queue_revision="queue",
    ).passed
    with pytest.raises(FinalGateError, match="HEAD is stale"):
        require_final_gate(path, expected_head="def")
    with pytest.raises(FinalGateError, match="plan digest"):
        require_final_gate(path, expected_head="abc", expected_plan_digest="other")
    with pytest.raises(FinalGateError, match="queue revision"):
        require_final_gate(path, expected_head="abc", expected_queue_revision="other")
    write_final_gate(path, record(canonical_test_status="fail"))
    with pytest.raises(FinalGateError, match="failed"):
        require_final_gate(path, expected_head="abc")


def test_missing_and_corrupt_final_gate_fail_closed(tmp_path):
    with pytest.raises(FinalGateError, match="missing"):
        require_final_gate(tmp_path / "missing.json", expected_head="abc")
    path = tmp_path / "gate.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(FinalGateError, match="corrupt"):
        require_final_gate(path, expected_head="abc")


def test_atomic_write_failure_preserves_previous_gate(tmp_path, monkeypatch):
    path = tmp_path / "gate.json"
    write_final_gate(path, record())
    original = path.read_bytes()
    monkeypatch.setattr("codex_flow.final_gate.os.replace", lambda *_args: (_ for _ in ()).throw(OSError("crash")))

    with pytest.raises(OSError, match="crash"):
        write_final_gate(path, record(reviewed_head="new"))

    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".gate.json.*.tmp"))


def test_producer_binds_terminal_exact_state_and_stores_only_evidence_hash(tmp_path):
    init_repo(tmp_path)
    plan = terminal_plan(tmp_path)
    produced = produce_final_gate(
        plan.plan_path,
        review_evidence={"status": "pass", "summary": "clean"},
        test_evidence={"status": "pass", "secret": "not-written-verbatim"},
    )

    queue = plans.load_queue(plan.plan_path)[1]
    payload = json.loads((plan.directory / "final-gate.json").read_text(encoding="utf-8"))
    assert produced.plan_digest == plan_file_digest(plan.plan_path)
    assert produced.terminal_queue_revision == terminal_queue_revision(queue)
    assert "not-written-verbatim" not in json.dumps(payload)
    assert FinalizeGuard.require(plan.plan_path).record == produced


def test_direct_merge_effect_is_blocked_without_final_gate(tmp_path, monkeypatch):
    init_repo(tmp_path)
    subprocess.run(["git", "switch", "-c", "codex/final-gate"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "feature.txt").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=tmp_path, check=True, capture_output=True)
    plan = terminal_plan(tmp_path)
    plan.plan_path.write_text(
        plan.plan_path.read_text(encoding="utf-8").replace("Branch: codex/final-gate", "Branch: codex/final-gate"),
        encoding="utf-8",
    )
    called = False

    def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("merge effect must not run")

    monkeypatch.setattr("codex_flow.merge.merge_branch", forbidden)
    result = MergeRunner().merge_local(plan.plan_path, execute=True)

    assert result.action == "needs_work"
    assert "final_gate" in result.message
    assert called is False
