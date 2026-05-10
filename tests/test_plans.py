from __future__ import annotations

import json

from codex_flow import plans, tickets


def test_create_plan_from_ticket_writes_plan_queue_and_handoff(tmp_path):
    ticket = tickets.submit_ticket("Codex Flow MVP 구현", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)

    assert plan.plan_path.exists()
    assert plan.queue_json.exists()
    assert plan.queue_md.exists()
    assert (plan.directory / "handoff.md").exists()

    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert queue["ticket_id"] == ticket.id
    assert queue["branch"].startswith("codex/")
    assert len(queue["units"]) == 3
    assert queue["units"][0]["status"] == "ready"
    assert "Branch: codex/" in plan.plan_path.read_text(encoding="utf-8")
    assert "### Commit 1:" in plan.plan_path.read_text(encoding="utf-8")


def test_mark_unit_updates_machine_and_markdown_queue(tmp_path):
    ticket = tickets.submit_ticket("마크 테스트", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)

    unit = plans.mark_unit(plan.plan_path, "unit-001", "done")

    assert unit["status"] == "done"
    queue_text = plan.queue_md.read_text(encoding="utf-8")
    assert "| unit-001 | done |" in queue_text
