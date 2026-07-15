from __future__ import annotations

from pathlib import Path

from codex_flow.implementer_agent import ImplementerAgentInput, build_implementation_prompt, build_review_prompt, parse_commit_unit_review, parse_review_gate
from codex_flow.merge_agent import parse_merge_agent_result
from codex_flow.plan_readiness import CommitUnit
from codex_flow.planner_agent import PlannerAgentInput, build_planner_prompt, parse_plan_written


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


def test_parse_review_gate_scores_and_blocks_important_findings():
    gate = parse_review_gate('REVIEW_GATE status="pass" blockers=0 important=0 minor=2 reason="minor polish remains"')

    assert gate is not None
    assert gate.passed
    assert gate.score == 90
    assert gate.to_dict()["minor"] == 2

    blocked = parse_commit_unit_review(
        "\n".join(
            [
                'REVIEW_GATE status="pass" blockers=0 important=1 minor=0 reason="important regression risk"',
                'COMMIT_UNIT_READY title="Done" summary="looks good"',
            ]
        )
    )

    assert blocked.status == "needs_work"
    assert blocked.gate is not None
    assert blocked.gate.important == 1
    assert "important regression risk" in blocked.reason


def test_parse_review_rejects_conflicting_terminal_signals_without_retry():
    review = parse_commit_unit_review(
        "\n".join(
            [
                'REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="clean"',
                'COMMIT_UNIT_READY title="Done" summary="ready"',
                'COMMIT_UNIT_NEEDS_WORK reason=""',
            ]
        )
    )

    assert review.status == "needs_work"
    assert review.retryable is False
    assert "exactly one" in review.reason


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
    assert "plan-first-implementation" in prompt
    assert "feature, UI/design/layout, refactor, integration, API/DB/routing" in prompt
    assert "status, review, briefing, QA-only, or test-only" in prompt
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
    assert "required skill is unavailable" not in prompt
    assert "Required skills are mandatory" in prompt
    assert "Optional skills may be skipped" in prompt


def test_commit_unit_review_prompt_requires_review_all_in_one_gate():
    prompt = build_review_prompt(
        ImplementerAgentInput(
            repo=Path("/tmp/repo"),
            plan_path=Path("/tmp/repo/.codex-flow/plans/demo/plan.md"),
            plan_content="## Commit Units\n\n### Commit 1: Build\n\nDo it",
            unit=CommitUnit(number=1, title="Build", content="Do it"),
            previous_commit=None,
            git_status="",
        )
    )

    assert "Mandatory post-unit review gate" in prompt
    assert "`review-all-in-one`" in prompt
    assert 'REVIEW_GATE status="pass|needs_work"' in prompt
    assert "blockers=0 important=0" in prompt
    assert "COMMIT_UNIT_NEEDS_WORK" in prompt
    assert "fallback" not in prompt
