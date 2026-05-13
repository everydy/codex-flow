from __future__ import annotations

import json

from codex_flow import plans, tickets
from codex_flow.planner_agent import PlannerAgentResult


class ManifestDroppingPlanner:
    def write_plan(self, input_data):
        input_data.plan_path.write_text(
            "\n".join(
                [
                    "# Codex Flow Plan: Planner Rewrite",
                    "",
                    f"Branch: {input_data.branch_name}",
                    f"Title: {input_data.plan_title}",
                    "",
                    "## Commit Units",
                    "",
                    "### Commit 1: Planner rewrite",
                    "",
                    "Do the work.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return PlannerAgentResult(
            path=str(input_data.plan_path),
            final_message=f'PLAN_WRITTEN path="{input_data.plan_path}"',
        )


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
    plan_text = plan.plan_path.read_text(encoding="utf-8")
    decisions_text = (plan.directory / "decisions.md").read_text(encoding="utf-8")
    assert "Branch: codex/" in plan_text
    assert "### Commit 1:" in plan_text
    assert "## Skill Routing Manifest" in plan_text
    assert "| Commit 1: 근거 수집과 범위 잠금 | `요청개선` |" in plan_text
    assert "| Final Gate | `review-all-in-one`, `qa-gate` |" in plan_text
    assert "unless the user explicitly approves" not in plan_text
    assert "finalize commands" in decisions_text
    assert queue["units"][0]["required_skills"] == ["요청개선"]


def test_create_plan_repairs_missing_manifest_after_planner_rewrite(tmp_path):
    ticket = tickets.submit_ticket("Planner manifest repair", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path, planner=ManifestDroppingPlanner())

    plan_text = plan.plan_path.read_text(encoding="utf-8")
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))

    assert "## Skill Routing Manifest" in plan_text
    assert plan_text.index("## Skill Routing Manifest") < plan_text.index("## Commit Units")
    assert "| Commit 1: Planner rewrite | `요청개선` |" in plan_text
    assert "| Final Gate | `review-all-in-one`, `qa-gate` |" in plan_text
    assert queue["units"][0]["title"] == "Planner rewrite"
    assert queue["units"][0]["required_skills"] == ["요청개선"]


def test_mark_unit_updates_machine_and_markdown_queue(tmp_path):
    ticket = tickets.submit_ticket("마크 테스트", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)

    unit = plans.mark_unit(plan.plan_path, "unit-001", "done")

    assert unit["status"] == "done"
    queue_text = plan.queue_md.read_text(encoding="utf-8")
    assert "| unit-001 | done |" in queue_text
