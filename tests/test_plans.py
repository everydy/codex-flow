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


class ImplementationManifestPlanner:
    def write_plan(self, input_data):
        input_data.plan_path.write_text(
            "\n".join(
                [
                    "# Codex Flow Plan: Planner Rewrite",
                    "",
                    f"Branch: {input_data.branch_name}",
                    f"Title: {input_data.plan_title}",
                    "",
                    "## Skill Routing Manifest",
                    "",
                    "| Phase | Required skills | Optional skills | Evidence |",
                    "| --- | --- | --- | --- |",
                    "| Commit 1: Build account report UI | `mission-completion-harness` | `디자인올인원` | UI implementation changes code. |",
                    "| Commit 2: Review only | `review-all-in-one` | `qa-gate` | Review and QA only. |",
                    "| Final Gate | `review-all-in-one`, `qa-gate` | `checkpoint` | Review and verification decide readiness. |",
                    "",
                    "## Commit Units",
                    "",
                    "### Commit 1: Build account report UI",
                    "",
                    "Do the work.",
                    "",
                    "### Commit 2: Review only",
                    "",
                    "Check the work.",
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
    handoff_text = (plan.directory / "handoff.md").read_text(encoding="utf-8")
    assert "Branch: codex/" in plan_text
    assert "### Commit 1:" in plan_text
    assert "## Skill Routing Manifest" in plan_text
    assert "| Commit 1: 근거 수집과 범위 잠금 | `요청개선`, `plan-first-implementation` |" in plan_text
    assert "| Commit 2: 좁은 구현 패치 | `plan-first-implementation`, `mission-completion-harness` |" in plan_text
    assert "| Final Gate | `review-all-in-one`, `qa-gate` |" in plan_text
    assert "unless the user explicitly approves" not in plan_text
    assert "locally merges into the target branch and closes the work branch" in decisions_text
    assert "run-all --plan <plan.md> --auto-resolve" in handoff_text
    assert "Single unit repair/manual step" in handoff_text
    assert "run-next --plan <plan.md> --auto-resolve" in handoff_text
    assert queue["units"][0]["required_skills"] == ["요청개선", "plan-first-implementation"]
    assert queue["units"][1]["required_skills"] == ["plan-first-implementation", "mission-completion-harness"]


def test_default_implementation_unit_allows_common_app_paths(tmp_path):
    ticket = tickets.submit_ticket("프런트엔드 화면 구현", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)

    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    implementation_unit = queue["units"][1]
    plan_text = plan.plan_path.read_text(encoding="utf-8")

    assert "frontend/**" in implementation_unit["allowed_paths"]
    assert "backend/**" in implementation_unit["allowed_paths"]
    assert "functions/**" in implementation_unit["allowed_paths"]
    assert "src/**" in implementation_unit["allowed_paths"]
    assert "tests/**" in implementation_unit["allowed_paths"]
    assert "frontend/**" in plan_text


def test_create_plan_repairs_missing_manifest_after_planner_rewrite(tmp_path):
    ticket = tickets.submit_ticket("Planner manifest repair", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path, planner=ManifestDroppingPlanner())

    plan_text = plan.plan_path.read_text(encoding="utf-8")
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))

    assert "## Skill Routing Manifest" in plan_text
    assert plan_text.index("## Skill Routing Manifest") < plan_text.index("## Commit Units")
    assert "| Commit 1: Planner rewrite | `요청개선`, `plan-first-implementation` |" in plan_text
    assert "| Final Gate | `review-all-in-one`, `qa-gate` |" in plan_text
    assert queue["units"][0]["title"] == "Planner rewrite"
    assert queue["units"][0]["required_skills"] == ["요청개선", "plan-first-implementation"]


def test_create_plan_repairs_existing_manifest_plan_first_policy(tmp_path):
    ticket = tickets.submit_ticket("Planner existing manifest repair", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path, planner=ImplementationManifestPlanner())

    plan_text = plan.plan_path.read_text(encoding="utf-8")
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))

    assert (
        "| Commit 1: Build account report UI | `plan-first-implementation`, `mission-completion-harness` |"
        in plan_text
    )
    assert "| Commit 2: Review only | `review-all-in-one` |" in plan_text
    assert queue["units"][0]["required_skills"] == ["plan-first-implementation", "mission-completion-harness"]
    assert queue["units"][1]["required_skills"] == ["review-all-in-one"]


def test_manifest_repair_does_not_skip_phase_word_inside_row(tmp_path):
    plan_dir = tmp_path / ".codex-flow" / "plans" / "phase-word"
    plan_dir.mkdir(parents=True)
    plan_path = plan_dir / "plan.md"
    plan_path.write_text(
        "\n".join(
            [
                "# Codex Flow Plan: Phase Word",
                "",
                "## Skill Routing Manifest",
                "",
                "| Phase | Required skills | Optional skills | Evidence |",
                "| --- | --- | --- | --- |",
                "| Commit 1: Phase 1 UI implementation | `mission-completion-harness` | `디자인올인원` | UI implementation changes code. |",
                "",
                "## Commit Units",
                "",
                "### Commit 1: Phase 1 UI implementation",
                "",
                "Do the work.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    changed = plans.ensure_plan_skill_routing_manifest(plan_path, {"units": []})

    assert changed is True
    assert (
        "| Commit 1: Phase 1 UI implementation | `plan-first-implementation`, `mission-completion-harness` |"
        in plan_path.read_text(encoding="utf-8")
    )


def test_mark_unit_updates_machine_and_markdown_queue(tmp_path):
    ticket = tickets.submit_ticket("마크 테스트", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)

    unit = plans.mark_unit(plan.plan_path, "unit-001", "done")

    assert unit["status"] == "done"
    queue_text = plan.queue_md.read_text(encoding="utf-8")
    assert "| unit-001 | done |" in queue_text
