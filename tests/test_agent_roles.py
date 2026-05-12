from __future__ import annotations

from codex_flow.implementer_agent import parse_commit_unit_review
from codex_flow.merge_agent import parse_merge_agent_result
from codex_flow.planner_agent import parse_plan_written
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
