from __future__ import annotations

import json
import subprocess

import pytest

from codex_flow import plans, tickets
from codex_flow.attempt_ledger import AttemptLedger, LedgerConflict
from codex_flow.main_unit import MainUnitError, begin_main_unit, complete_main_unit, hold_main_unit


def init_repo(path):
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "switch", "-c", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "codex-flow@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Codex Flow"], cwd=path, check=True)
    (path / "README.md").write_text("# Test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)


def make_plan(path):
    ticket = tickets.submit_ticket("Main transaction", repo=path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=path)
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    queue["units"][0]["allowed_paths"] = ["work.txt"]
    plan.queue_json.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan


def test_main_unit_exact_commits_allowed_paths(tmp_path):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)

    opened = begin_main_unit(plan.plan_path)
    (tmp_path / "work.txt").write_text("implemented\n", encoding="utf-8")
    completed = complete_main_unit(
        plan.plan_path,
        unit_id=opened.unit_id,
        expected_revision=opened.ledger_revision,
        evidence={"status": "pass", "checks": ["focused"]},
        message="feat: main transaction",
    )

    assert completed.status == "completed"
    assert completed.changed_paths == ("work.txt",)
    assert completed.commit
    assert subprocess.run(
        ["git", "show", "--pretty=", "--name-only", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines() == ["work.txt"]
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert queue["units"][0]["status"] == "done"


def test_main_unit_rejects_stale_revision_and_replay(tmp_path):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    opened = begin_main_unit(plan.plan_path)
    (tmp_path / "work.txt").write_text("implemented\n", encoding="utf-8")

    with pytest.raises(MainUnitError, match="revision"):
        complete_main_unit(
            plan.plan_path,
            unit_id=opened.unit_id,
            expected_revision=opened.ledger_revision + 1,
            evidence={"status": "pass"},
            message="feat: stale",
        )

    complete_main_unit(
        plan.plan_path,
        unit_id=opened.unit_id,
        expected_revision=opened.ledger_revision,
        evidence={"status": "pass"},
        message="feat: complete",
    )
    resumed = begin_main_unit(plan.plan_path, unit_id=opened.unit_id)
    assert resumed.status == "completed"
    assert resumed.ledger_revision == 3


def test_main_unit_rejects_head_drift_without_rollback(tmp_path):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    opened = begin_main_unit(plan.plan_path)
    (tmp_path / "other.txt").write_text("other\n", encoding="utf-8")
    subprocess.run(["git", "add", "other.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "other"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "work.txt").write_text("candidate\n", encoding="utf-8")

    with pytest.raises(MainUnitError, match="HEAD"):
        complete_main_unit(
            plan.plan_path,
            unit_id=opened.unit_id,
            expected_revision=opened.ledger_revision,
            evidence={"status": "pass"},
            message="feat: rejected",
        )

    assert (tmp_path / "work.txt").read_text(encoding="utf-8") == "candidate\n"


@pytest.mark.parametrize("tracked", [False, True])
def test_main_unit_rejects_out_of_scope_changes_and_preserves_them(tmp_path, tracked):
    init_repo(tmp_path)
    if tracked:
        (tmp_path / "outside.txt").write_text("before\n", encoding="utf-8")
        subprocess.run(["git", "add", "outside.txt"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "track outside"], cwd=tmp_path, check=True, capture_output=True)
    plan = make_plan(tmp_path)
    opened = begin_main_unit(plan.plan_path)
    (tmp_path / "work.txt").write_text("candidate\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside\n", encoding="utf-8")

    with pytest.raises(MainUnitError, match="out-of-scope"):
        complete_main_unit(
            plan.plan_path,
            unit_id=opened.unit_id,
            expected_revision=opened.ledger_revision,
            evidence={"status": "pass"},
            message="feat: rejected",
        )

    assert (tmp_path / "outside.txt").read_text(encoding="utf-8") == "outside\n"


def test_hold_main_unit_preserves_candidate_and_records_main_recovery(tmp_path):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    opened = begin_main_unit(plan.plan_path)
    (tmp_path / "work.txt").write_text("candidate\n", encoding="utf-8")

    held = hold_main_unit(
        plan.plan_path,
        unit_id=opened.unit_id,
        expected_revision=opened.ledger_revision,
        failure_class="environment",
        reason="test service unavailable",
    )

    assert held.status == "held"
    assert (tmp_path / "work.txt").read_text(encoding="utf-8") == "candidate\n"
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert queue["units"][0]["status"] == "needs_work"
    assert queue["units"][0]["recovery_owner"] == "main"


def test_held_candidate_requires_explicit_retry_and_uses_new_attempt(tmp_path):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    opened = begin_main_unit(plan.plan_path)
    (tmp_path / "work.txt").write_text("candidate\n", encoding="utf-8")
    hold_main_unit(
        plan.plan_path,
        unit_id=opened.unit_id,
        expected_revision=opened.ledger_revision,
        failure_class="environment",
        reason="service unavailable",
    )

    with pytest.raises(MainUnitError, match="--retry-held"):
        begin_main_unit(plan.plan_path, unit_id=opened.unit_id)

    retried = begin_main_unit(plan.plan_path, unit_id=opened.unit_id, retry_held=True)
    assert retried.attempt == 2
    assert retried.ledger_revision == 1
    assert "attempt-2" in str(retried.ledger_path)
    assert (tmp_path / "work.txt").read_text(encoding="utf-8") == "candidate\n"
    events, _ = AttemptLedger(retried.ledger_path).recover_events()
    assert events[-1]["event"] == "main_unit_retry_opened"


def test_retry_refuses_changed_held_candidate(tmp_path):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    opened = begin_main_unit(plan.plan_path)
    (tmp_path / "work.txt").write_text("candidate\n", encoding="utf-8")
    hold_main_unit(
        plan.plan_path,
        unit_id=opened.unit_id,
        expected_revision=opened.ledger_revision,
        failure_class="repairable_in_scope",
        reason="needs repair",
    )
    (tmp_path / "work.txt").write_text("different\n", encoding="utf-8")

    with pytest.raises(MainUnitError, match="held candidate changed"):
        begin_main_unit(plan.plan_path, unit_id=opened.unit_id, retry_held=True)


def test_retry_refuses_failure_that_requires_new_scope(tmp_path):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    opened = begin_main_unit(plan.plan_path)
    (tmp_path / "work.txt").write_text("candidate\n", encoding="utf-8")
    hold_main_unit(
        plan.plan_path,
        unit_id=opened.unit_id,
        expected_revision=opened.ledger_revision,
        failure_class="repairable_new_scope",
        reason="another file is required",
    )

    with pytest.raises(MainUnitError, match="requires replanning"):
        begin_main_unit(plan.plan_path, unit_id=opened.unit_id, retry_held=True)


def test_complete_resumes_commit_after_ledger_finalize_crash(tmp_path, monkeypatch):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    opened = begin_main_unit(plan.plan_path)
    (tmp_path / "work.txt").write_text("implemented\n", encoding="utf-8")
    original = AttemptLedger.compare_and_set
    failed = False

    def crash_before_completed(self, expected_revision, record):
        nonlocal failed
        if record.get("status") == "completed" and not failed:
            failed = True
            raise OSError("simulated ledger finalize crash")
        return original(self, expected_revision, record)

    monkeypatch.setattr(AttemptLedger, "compare_and_set", crash_before_completed)
    with pytest.raises(OSError, match="ledger finalize crash"):
        complete_main_unit(
            plan.plan_path,
            unit_id=opened.unit_id,
            expected_revision=opened.ledger_revision,
            evidence={"status": "pass"},
            message="feat: crash safe",
        )
    committed_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()

    resumed = begin_main_unit(plan.plan_path, unit_id=opened.unit_id)
    assert resumed.status == "committing"
    completed = complete_main_unit(
        plan.plan_path,
        unit_id=opened.unit_id,
        expected_revision=resumed.ledger_revision,
        evidence={"status": "pass"},
        message="feat: crash safe",
    )
    assert completed.commit == committed_head
    assert subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip() == "2"


def test_committing_cas_conflict_does_not_mutate_real_index(tmp_path, monkeypatch):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    opened = begin_main_unit(plan.plan_path)
    (tmp_path / "work.txt").write_text("candidate\n", encoding="utf-8")
    original = AttemptLedger.compare_and_set

    def conflict_on_prepare(self, expected_revision, record):
        if record.get("status") == "committing":
            raise LedgerConflict("simulated CAS loss")
        return original(self, expected_revision, record)

    monkeypatch.setattr(AttemptLedger, "compare_and_set", conflict_on_prepare)
    with pytest.raises(MainUnitError, match="simulated CAS loss"):
        complete_main_unit(
            plan.plan_path,
            unit_id=opened.unit_id,
            expected_revision=opened.ledger_revision,
            evidence={"status": "pass"},
            message="feat: no index mutation",
        )

    assert subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout == ""
    assert AttemptLedger(opened.ledger_path).load().record["status"] == "open"


def test_completed_ledger_reconciles_missing_queue_save(tmp_path, monkeypatch):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    opened = begin_main_unit(plan.plan_path)
    (tmp_path / "work.txt").write_text("implemented\n", encoding="utf-8")
    original = plans.save_queue
    failed = False

    def fail_once(plan_dir, queue):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated queue save crash")
        return original(plan_dir, queue)

    monkeypatch.setattr(plans, "save_queue", fail_once)
    with pytest.raises(OSError, match="queue save crash"):
        complete_main_unit(
            plan.plan_path,
            unit_id=opened.unit_id,
            expected_revision=opened.ledger_revision,
            evidence={"status": "pass"},
            message="feat: reconcile queue",
        )

    resumed = begin_main_unit(plan.plan_path, unit_id=opened.unit_id)
    assert resumed.status == "completed"
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert queue["units"][0]["status"] == "done"
    events, _ = AttemptLedger(resumed.ledger_path).recover_events()
    assert any(event["event"] == "main_unit_reconciled" for event in events)


def test_open_ledger_reconciles_begin_queue_save_failure(tmp_path, monkeypatch):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    original = plans.save_queue
    failed = False

    def fail_once(plan_dir, queue):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated begin queue save crash")
        return original(plan_dir, queue)

    monkeypatch.setattr(plans, "save_queue", fail_once)
    with pytest.raises(OSError, match="begin queue save crash"):
        begin_main_unit(plan.plan_path)

    resumed = begin_main_unit(plan.plan_path)
    assert resumed.status == "open"
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert queue["units"][0]["status"] == "in_progress"
    events, _ = AttemptLedger(resumed.ledger_path).recover_events()
    assert events[-1]["event"] == "main_unit_reconciled"


def test_held_ledger_reconciles_hold_queue_save_failure(tmp_path, monkeypatch):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    opened = begin_main_unit(plan.plan_path)
    (tmp_path / "work.txt").write_text("candidate\n", encoding="utf-8")
    original = plans.save_queue
    failed = False

    def fail_once(plan_dir, queue):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated hold queue save crash")
        return original(plan_dir, queue)

    monkeypatch.setattr(plans, "save_queue", fail_once)
    with pytest.raises(OSError, match="hold queue save crash"):
        hold_main_unit(
            plan.plan_path,
            unit_id=opened.unit_id,
            expected_revision=opened.ledger_revision,
            failure_class="environment",
            reason="service unavailable",
        )

    with pytest.raises(MainUnitError, match="--retry-held"):
        begin_main_unit(plan.plan_path, unit_id=opened.unit_id)
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert queue["units"][0]["status"] == "needs_work"
    events, _ = AttemptLedger(opened.ledger_path).recover_events()
    assert events[-1]["event"] == "main_unit_reconciled"


def test_same_parent_paths_and_message_with_different_content_is_held(tmp_path, monkeypatch):
    init_repo(tmp_path)
    plan = make_plan(tmp_path)
    opened = begin_main_unit(plan.plan_path)
    (tmp_path / "work.txt").write_text("expected\n", encoding="utf-8")

    def crash_commit(*_args, **_kwargs):
        raise SystemExit("simulated commit crash")

    monkeypatch.setattr("codex_flow.main_unit.commit_paths", crash_commit)
    with pytest.raises(MainUnitError, match="simulated commit crash"):
        complete_main_unit(
            plan.plan_path,
            unit_id=opened.unit_id,
            expected_revision=opened.ledger_revision,
            evidence={"status": "pass"},
            message="feat: exact content",
        )

    monkeypatch.undo()
    (tmp_path / "work.txt").write_text("different\n", encoding="utf-8")
    subprocess.run(["git", "add", "work.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "feat: exact content"], cwd=tmp_path, check=True, capture_output=True)
    divergent_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()

    resumed = begin_main_unit(plan.plan_path, unit_id=opened.unit_id)
    with pytest.raises(MainUnitError, match="ambiguous_content"):
        complete_main_unit(
            plan.plan_path,
            unit_id=opened.unit_id,
            expected_revision=resumed.ledger_revision,
            evidence={"status": "pass"},
            message="feat: exact content",
        )
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip() == divergent_head


def test_exact_commit_preserves_unrelated_staged_change(tmp_path):
    init_repo(tmp_path)
    (tmp_path / "outside.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "outside.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "outside base"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "outside.txt").write_text("staged unrelated\n", encoding="utf-8")
    subprocess.run(["git", "add", "outside.txt"], cwd=tmp_path, check=True)
    plan = make_plan(tmp_path)
    opened = begin_main_unit(plan.plan_path)
    (tmp_path / "work.txt").write_text("implemented\n", encoding="utf-8")

    complete_main_unit(
        plan.plan_path,
        unit_id=opened.unit_id,
        expected_revision=opened.ledger_revision,
        evidence={"status": "pass"},
        message="feat: exact path only",
    )

    committed = subprocess.run(
        ["git", "show", "--pretty=", "--name-only", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert committed == ["work.txt"]
    assert "outside.txt" in subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.splitlines()
