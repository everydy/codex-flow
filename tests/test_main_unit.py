from __future__ import annotations

import json
import subprocess

import pytest

from codex_flow import plans, tickets
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
    with pytest.raises(MainUnitError, match="already"):
        begin_main_unit(plan.plan_path, unit_id=opened.unit_id)


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
