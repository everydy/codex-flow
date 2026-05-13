from __future__ import annotations

from pathlib import Path

from codex_flow.implementer_agent import ImplementerAgentInput, build_implementation_prompt, parse_commit_unit_review
from codex_flow.merge_agent import parse_merge_agent_result
from codex_flow.plan_readiness import CommitUnit
from codex_flow.planner_agent import PlannerAgentInput, build_planner_prompt, parse_plan_written
from codex_flow.router_agent import parse_route_decision


def test_parse_route_decision_variants():
    existing = parse_route_decision('notes\nROUTE existing_plan planPath=".codex-flow/plans/demo/plan.md" reason="same work"\n')
    new = parse_route_decision('ROUTE new_plan branchName="codex/demo" planTitle="Demo" reason="new work"\n')
    paused = parse_route_decision('ROUTE pause_for_pr_review reason="locked"\n')

    assert existing.action == "existing_plan"
    assert existing.plan_path.endswith("plan.md")
    assert new.branch_name == "codex/demo"
    assert new.plan_title == "Demo"
    assert paused.action == "pause_for_pr_review"


def test_parse_planner_implementer_and_merge_final_lines():
    assert parse_plan_written('PLAN_WRITTEN path=".codex-flow/plans/demo/plan.md"') == ".codex-flow/plans/demo/plan.md"

    ready = parse_commit_unit_review('COMMIT_UNIT_READY title="Done" summary="ok"')
    needs_work = parse_commit_unit_review('COMMIT_UNIT_NEEDS_WORK reason="tests failed"')
    merge_ready = parse_merge_agent_result('MERGE_READY summary="resolved"')
    merge_needs_work = parse_merge_agent_result('MERGE_NEEDS_WORK reason="manual needed"')

    assert ready.status == "ready"
    assert ready.summary == "ok"
    assert needs_work.status == "needs_work"
    assert merge_ready.status == "ready"
    assert merge_needs_work.reason == "manual needed"


def test_planner_prompt_requires_skill_routing_manifest(tmp_path):
    prompt = build_planner_prompt(
        PlannerAgentInput(
            repo=tmp_path,
            branch_name="codex/demo",
            plan_title="Demo",
            plan_path=tmp_path / ".codex-flow" / "plans" / "demo" / "plan.md",
            queue_path=tmp_path / ".codex-flow" / "plans" / "demo" / "queue.md",
            log_path=tmp_path / ".codex-flow" / "plans" / "demo" / "log.md",
            prompt="demo",
            reason="test",
        )
    )

    assert "## Skill Routing Manifest" in prompt
    assert "Phase | Required skills | Optional skills | Evidence" in prompt
    assert "review-all-in-one" in prompt
    assert "qa-gate" in prompt


def test_implementer_prompt_includes_selected_skill_routing_manifest_entry():
    plan_content = "\n".join(
        [
            "## Skill Routing Manifest",
            "",
            "| Phase | Required skills | Optional skills | Evidence |",
            "| --- | --- | --- | --- |",
            "| Commit 2: Build | `mission-completion-harness` | `디자인올인원` | 구현 단위 |",
            "",
            "## Commit Units",
            "",
            "### Commit 2: Build",
            "",
            "Do it",
        ]
    )
    prompt = build_implementation_prompt(
        ImplementerAgentInput(
            repo=Path("/tmp/repo"),
            plan_path=Path("/tmp/repo/.codex-flow/plans/demo/plan.md"),
            plan_content=plan_content,
            unit=CommitUnit(number=2, title="Build", content="Do it"),
            previous_commit=None,
            git_status="",
        )
    )

    assert "Skill Routing Manifest entry" in prompt
    assert "Required skills: `mission-completion-harness`" in prompt
    assert "Optional skills: `디자인올인원`" in prompt
