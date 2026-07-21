from __future__ import annotations

import json

from codex_flow import cli, dashboard, plans, tickets
from codex_flow.attempt_ledger import AttemptLedger
from codex_flow.cancellation import cancellation_requested, request_cancel


def make_running_attempt(tmp_path):
    ticket = tickets.submit_ticket("Cancellation dashboard", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    unit = queue["units"][0]
    unit.update(
        {
            "status": "in_progress",
            "execution_owner": "isolated-child",
            "repair_attempts": 0,
            "changed_paths": [f"src/file-{index}.py" for index in range(12)],
        }
    )
    plan.queue_json.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    attempt_dir = plan.directory / "attempts" / unit["id"] / "attempt-0"
    ledger = AttemptLedger(attempt_dir / "attempt-ledger.json")
    ledger.compare_and_set(
        0,
        {
            "status": "running",
            "phase": "implementation",
            "changed_paths": unit["changed_paths"],
        },
    )
    return plan, unit, attempt_dir


def test_cancel_request_is_attempt_bound_and_stale_marker_is_ignored(tmp_path):
    plan, unit, attempt_dir = make_running_attempt(tmp_path)

    marker = request_cancel(plan.plan_path, unit_id=unit["id"], attempt=0)

    assert marker == attempt_dir / "cancel-request.json"
    assert cancellation_requested(attempt_dir, unit_id=unit["id"], attempt=0) is True
    assert cancellation_requested(attempt_dir, unit_id=unit["id"], attempt=1) is False
    assert cancellation_requested(attempt_dir, unit_id="other-unit", attempt=0) is False


def test_dashboard_shows_bounded_files_cost_truth_and_real_cancel_command(tmp_path, monkeypatch):
    plan, unit, attempt_dir = make_running_attempt(tmp_path)
    monkeypatch.setenv(
        "CODEX_FLOW_OPERATOR_COMMAND_PREFIX",
        "python3 /plugin/implementation_commit.py --repo /target --action",
    )
    implementation = attempt_dir / "implementation"
    review = attempt_dir / "review"
    implementation.mkdir()
    review.mkdir()
    (implementation / "metadata.json").write_text("{}\n", encoding="utf-8")
    (review / "metadata.json").write_text("{}\n", encoding="utf-8")

    rendered = "\n".join(dashboard.format_current_unit(plan.directory, unit))

    assert "(+2 more)" in rendered
    assert "Agent calls: 2" in rendered
    assert "Token usage: not recorded" in rendered
    assert "request-cancel" in rendered
    assert "--attempt 0" in rendered
    assert "python3 /plugin/implementation_commit.py --repo /target --action request-cancel" in rendered
    assert "scripts/codex_flow.py" not in rendered


def test_request_cancel_cli_contract_is_explicit():
    args = cli.build_parser().parse_args(
        ["request-cancel", "--plan", "plan.md", "--unit", "unit-001", "--attempt", "2"]
    )

    assert args.command == "request-cancel"
    assert args.attempt == 2
