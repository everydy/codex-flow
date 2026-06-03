from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .codex_cli import fence, last_matching_line, parse_key_values, parse_session_id, run_codex_exec
from . import plan_readiness
from .plan_readiness import CommitUnit


@dataclass(frozen=True)
class CommitUnitReview:
    status: str
    title: str = ""
    summary: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ImplementerAgentInput:
    repo: Path
    plan_path: Path
    plan_content: str
    unit: CommitUnit
    previous_commit: str | None
    git_status: str
    repair_attempt: int = 0
    repair_reason: str = ""


@dataclass(frozen=True)
class ImplementerAgentResult:
    session_id: str
    implementation_message: str
    review_message: str
    review: CommitUnitReview


class ImplementerAgent(Protocol):
    def implement(self, input_data: ImplementerAgentInput) -> ImplementerAgentResult:
        ...


POST_UNIT_REVIEW_SKILL = "review-all-in-one"


class CodexImplementerAgent:
    def __init__(self, command: str = "codex", extra_args: list[str] | None = None) -> None:
        self.command = command
        self.extra_args = extra_args or []

    def implement(self, input_data: ImplementerAgentInput) -> ImplementerAgentResult:
        implementation = run_codex_exec(
            build_implementation_prompt(input_data),
            repo=input_data.repo,
            command=self.command,
            sandbox="workspace-write",
            extra_args=self.extra_args,
        )
        session_id = parse_session_id(implementation.stdout) or ""
        if not session_id:
            raise SystemExit("Codex implementer did not report a session id")
        review = run_codex_exec(
            build_review_prompt(input_data),
            repo=input_data.repo,
            command=self.command,
            sandbox="workspace-write",
            extra_args=self.extra_args,
            resume_session_id=session_id,
        )
        return ImplementerAgentResult(
            session_id=session_id,
            implementation_message=implementation.final_message,
            review_message=review.final_message,
            review=parse_commit_unit_review(review.final_message),
        )


def build_implementation_prompt(input_data: ImplementerAgentInput) -> str:
    try:
        plan_path = str(input_data.plan_path.relative_to(input_data.repo))
    except ValueError:
        plan_path = str(input_data.plan_path)
    skill_routing = plan_readiness.format_skill_routing_entry(
        plan_readiness.skill_routing_for_commit(input_data.plan_content, input_data.unit.number)
    )
    return "\n".join(
        [
            f"Read {plan_path} and implement only commit unit {input_data.unit.number}.",
            "",
            "You are Agent 2: Implementer for the Codex Flow workflow orchestrator.",
            "Implement only the selected commit unit. Do not create a git commit.",
            "Before editing, load and apply the required skills named in the Skill Routing Manifest when available.",
            "If a required skill is unavailable, continue with the closest safe fallback and mention the fallback in the review summary.",
            "",
            "Selected commit unit:",
            fence(f"### Commit {input_data.unit.number}: {input_data.unit.title}\n\n{input_data.unit.content}"),
            "",
            "Skill Routing Manifest entry:",
            fence(skill_routing),
            "",
            "Previous commit:",
            input_data.previous_commit or "None",
            "",
            "Git status before this unit:",
            fence(input_data.git_status) if input_data.git_status.strip() else "Clean",
            "",
            *repair_context(input_data),
            "Unit boundary gates:",
            "- Do not create or merge a real remote PR inside this commit-unit implementation; finalization commands handle PR and merge after all units are ready.",
            "- Do not deploy.",
            "- Do not reset, checkout, or revert unrelated user changes.",
            "- If secrets, accounts, payments, or external posting are required, stop after preparing drafts.",
            "",
            "Full plan.md:",
            fence(input_data.plan_content),
        ]
    )


def repair_context(input_data: ImplementerAgentInput) -> list[str]:
    if input_data.repair_attempt <= 0:
        return []
    return [
        "Repair attempt:",
        fence(
            "\n".join(
                [
                    f"Attempt: {input_data.repair_attempt}",
                    f"Previous needs_work reason: {input_data.repair_reason or 'Not provided'}",
                    "Keep any useful existing working tree changes from the previous attempt.",
                    "Do not restart the unit from scratch unless the current partial changes are directly wrong.",
                    "Focus only on resolving the reason above and returning the commit unit to a ready state.",
                ]
            )
        ),
        "",
    ]


def build_review_prompt(input_data: ImplementerAgentInput) -> str:
    return "\n".join(
        [
            "Review the implementation for the current commit unit and make focused fixes if needed.",
            "Do not create a git commit; Codex Flow will commit after your review.",
            "",
            f"Mandatory post-unit review gate: load and apply `{POST_UNIT_REVIEW_SKILL}` before returning `COMMIT_UNIT_READY`.",
            "Treat blocker or important findings from that review as commit blockers: fix them inside this same review pass when safe, or return `COMMIT_UNIT_NEEDS_WORK` with the reason.",
            f"If `{POST_UNIT_REVIEW_SKILL}` is unavailable, return `COMMIT_UNIT_NEEDS_WORK` and explain the missing review gate.",
            "",
            "Return exactly one final line in one of these forms:",
            f'COMMIT_UNIT_READY title="{input_data.unit.title}" summary="..."',
            'COMMIT_UNIT_NEEDS_WORK reason="..."',
            "",
            "Review requirement: confirm that required skills from the Skill Routing Manifest were applied or explicitly skipped with a fallback reason.",
        ]
    )


def parse_commit_unit_review(text: str) -> CommitUnitReview:
    line = last_matching_line(text, "COMMIT_UNIT_")
    values = parse_key_values(line)
    if line.startswith("COMMIT_UNIT_READY"):
        return CommitUnitReview("ready", title=values.get("title", "Commit unit ready"), summary=values.get("summary", "Ready to commit."))
    if line.startswith("COMMIT_UNIT_NEEDS_WORK"):
        return CommitUnitReview("needs_work", reason=values.get("reason", "Implementer requested more work."))
    raise ValueError(f"Unknown commit unit review: {line}")
