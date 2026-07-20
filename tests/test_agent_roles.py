from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import codex_flow.implementer_agent as implementer_module
import codex_flow.reviewer_agent as reviewer_module
from codex_flow.codex_cli import CodexExecFailure
from codex_flow.implementer_agent import CodexImplementerAgent, ImplementerAgentInput, build_implementation_prompt
from codex_flow.merge_agent import parse_merge_agent_result
from codex_flow.plan_readiness import CommitUnit
from codex_flow.planner_agent import PlannerAgentInput, build_planner_prompt, parse_plan_written
from codex_flow.reviewer_agent import (
    CodexReadOnlyReviewer,
    ReviewerAgentInput,
    ReviewerProcessFailure,
    build_read_only_review_prompt,
    parse_commit_unit_review,
    parse_review_gate,
    require_internal_review,
    review_control_plane_exclusions,
)


def init_review_repo(path: Path) -> tuple[str, str]:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "codex-flow@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Codex Flow"], cwd=path, check=True)
    (path / "README.md").write_text("# Test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)
    return reviewer_module.head_summary(path) or "", reviewer_module.candidate_diff_digest(path)


def reviewer_input(path: Path, head: str, digest: str) -> ReviewerAgentInput:
    return ReviewerAgentInput(
        repo=path,
        plan_path=path / "plan.md",
        plan_content="### Commit 1: Build\n\nDo it",
        unit=CommitUnit(number=1, title="Build", content="Do it"),
        implementation_session_id="implementation-session",
        expected_head=head,
        expected_full_diff_digest=digest,
    )


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
    gate = parse_review_gate('INTERNAL_REVIEW_GATE status="pass" blockers=0 important=0 minor=2 reason="minor polish remains"')

    assert gate is not None
    assert gate.passed
    assert gate.score == 90
    assert gate.to_dict()["minor"] == 2

    blocked = parse_commit_unit_review(
        "\n".join(
            [
                'INTERNAL_REVIEW_GATE status="pass" blockers=0 important=1 minor=0 reason="important regression risk"',
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
                'INTERNAL_REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="clean"',
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


def test_read_only_review_prompt_forbids_repair_and_requires_internal_gate(tmp_path):
    prompt = build_read_only_review_prompt(
        ReviewerAgentInput(
            repo=Path("/tmp/repo"),
            plan_path=Path("/tmp/repo/.codex-flow/plans/demo/plan.md"),
            plan_content="## Commit Units\n\n### Commit 1: Build\n\nDo it",
            unit=CommitUnit(number=1, title="Build", content="Do it"),
            implementation_session_id="implementation-session",
            expected_head="head",
            expected_full_diff_digest="digest",
        )
    )

    assert "Agent 3: Read-only Reviewer" in prompt
    assert "Do not edit, repair" in prompt
    assert "review-all-in-one" not in prompt
    assert 'INTERNAL_REVIEW_GATE status="pass|needs_work"' in prompt
    assert "blockers=0 important=0" in prompt
    assert "COMMIT_UNIT_NEEDS_WORK" in prompt


def test_implementer_preserves_persisted_high_risk_profile(monkeypatch, tmp_path):
    observed_profiles = []

    def fake_exec(*args, **kwargs):
        observed_profiles.append(kwargs["execution_profile"].value)
        return SimpleNamespace(stdout='{"session_id":"session-1"}\n', final_message="implemented")

    monkeypatch.setattr(implementer_module, "run_codex_exec", fake_exec)
    agent = CodexImplementerAgent()

    agent.implement(
        ImplementerAgentInput(
            repo=tmp_path,
            plan_path=tmp_path / "plan.md",
            plan_content="### Commit 1: Neutral\n\nNo signal",
            unit=CommitUnit(number=1, title="Neutral", content="No signal"),
            previous_commit=None,
            git_status="",
            execution_policy={"declared_profile": "high_risk", "effective_profile": "high_risk"},
            allowed_paths=("README.md",),
        )
    )

    assert observed_profiles == ["high_risk"]


def test_reviewer_uses_fresh_read_only_session_and_preserves_bytes(monkeypatch, tmp_path):
    expected_head, expected_digest = init_review_repo(tmp_path)
    observed = {}

    def fake_exec(*args, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(
            stdout='{"session_id":"review-session"}\n',
            final_message=(
                'INTERNAL_REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="clean"\n'
                'COMMIT_UNIT_READY title="Done" summary="ok"'
            ),
        )

    monkeypatch.setattr(reviewer_module, "run_codex_exec", fake_exec)
    result = CodexReadOnlyReviewer().review(
        reviewer_input(tmp_path, expected_head, expected_digest)
    )

    assert observed["sandbox"] == "read-only"
    assert "resume_session_id" not in observed
    assert observed["phase"] == "review"
    assert result.reviewer_session_id == "review-session"
    assert result.review.status == "ready"
    assert result.head_unchanged
    assert result.full_diff_digest_unchanged


@pytest.mark.parametrize("mutate", [False, True])
def test_reviewer_process_failure_always_checks_candidate_invariants(monkeypatch, tmp_path, mutate):
    expected_head, expected_digest = init_review_repo(tmp_path)

    def fail_exec(*_args, **_kwargs):
        if mutate:
            (tmp_path / "reviewer-write.txt").write_text("forbidden\n", encoding="utf-8")
        raise CodexExecFailure(7, tmp_path / "diagnostics")

    monkeypatch.setattr(reviewer_module, "run_codex_exec", fail_exec)
    with pytest.raises(ReviewerProcessFailure) as captured:
        CodexReadOnlyReviewer().review(reviewer_input(tmp_path, expected_head, expected_digest))

    assert captured.value.head_unchanged is True
    assert captured.value.full_diff_digest_unchanged is (not mutate)
    if mutate:
        assert "mutation detected" in str(captured.value)
    else:
        assert "invariants preserved" in str(captured.value)


def test_reviewer_rejects_current_implementation_session_reuse(monkeypatch, tmp_path):
    expected_head, expected_digest = init_review_repo(tmp_path)
    monkeypatch.setattr(
        reviewer_module,
        "run_codex_exec",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout='{"session_id":"implementation-session"}\n',
            final_message=(
                'INTERNAL_REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="clean"\n'
                'COMMIT_UNIT_READY title="Done" summary="ok"'
            ),
        ),
    )

    result = CodexReadOnlyReviewer().review(reviewer_input(tmp_path, expected_head, expected_digest))

    assert result.review.status == "needs_work"
    assert result.review.retryable is False
    assert "reused" in result.review.reason


def test_needs_work_without_complete_internal_gate_is_protocol_failure():
    message = 'COMMIT_UNIT_NEEDS_WORK reason="real finding but missing gate"'

    review = require_internal_review(parse_commit_unit_review(message), message)

    assert review.status == "needs_work"
    assert review.retryable is False
    assert review.failure_kind == "protocol_failure"
    assert "missing internal review evidence" in review.reason


def test_invalid_internal_gate_status_is_nonretryable_protocol_failure():
    message = "\n".join(
        [
            'INTERNAL_REVIEW_GATE status="banana" blockers=0 important=0 minor=0 reason="invalid status"',
            'COMMIT_UNIT_NEEDS_WORK reason="invalid status"',
        ]
    )

    review = require_internal_review(parse_commit_unit_review(message), message)

    assert review.status == "needs_work"
    assert review.retryable is False
    assert review.failure_kind == "protocol_failure"
    assert "invalid status: banana" in review.reason


def test_candidate_digest_excludes_only_active_parent_control_files(tmp_path):
    init_review_repo(tmp_path)
    attempt_dir = tmp_path / ".codex-flow" / "attempts" / "unit-1" / "attempt-0"
    review_dir = attempt_dir / "review"
    review_dir.mkdir(parents=True)
    ledger = attempt_dir / "attempt-ledger.json"
    ledger.write_text("{}\n", encoding="utf-8")
    (attempt_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / ".codex-flow" / "plan.md").write_text("original\n", encoding="utf-8")
    exclusions = review_control_plane_exclusions(tmp_path, attempt_dir, ledger)
    before = reviewer_module.candidate_diff_digest(tmp_path, exclusions)

    (review_dir / "metadata.json").write_text('{"status":"completed"}\n', encoding="utf-8")
    ledger.write_text('{"revision":2}\n', encoding="utf-8")
    assert reviewer_module.candidate_diff_digest(tmp_path, exclusions) == before

    (tmp_path / ".codex-flow" / "plan.md").write_text("mutated\n", encoding="utf-8")
    assert reviewer_module.candidate_diff_digest(tmp_path, exclusions) != before
