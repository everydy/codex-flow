from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .codex_cli import fence, last_matching_line, parse_key_values, parse_session_id, run_codex_exec
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


@dataclass(frozen=True)
class ImplementerAgentResult:
    session_id: str
    implementation_message: str
    review_message: str
    review: CommitUnitReview


class ImplementerAgent(Protocol):
    def implement(self, input_data: ImplementerAgentInput) -> ImplementerAgentResult:
        ...


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
    return "\n".join(
        [
            f"Read {plan_path} and implement only commit unit {input_data.unit.number}.",
            "",
            "You are Agent 2: Implementer for the Codex Flow workflow orchestrator.",
            "Implement only the selected commit unit. Do not create a git commit.",
            "",
            "Selected commit unit:",
            fence(f"### Commit {input_data.unit.number}: {input_data.unit.title}\n\n{input_data.unit.content}"),
            "",
            "Previous commit:",
            input_data.previous_commit or "None",
            "",
            "Git status before this unit:",
            fence(input_data.git_status) if input_data.git_status.strip() else "Clean",
            "",
            "Hard stop gates:",
            "- Do not create or merge a real remote PR.",
            "- Do not deploy.",
            "- Do not reset, checkout, or revert unrelated user changes.",
            "- If secrets, accounts, payments, or external posting are required, stop after preparing drafts.",
            "",
            "Full plan.md:",
            fence(input_data.plan_content),
        ]
    )


def build_review_prompt(input_data: ImplementerAgentInput) -> str:
    return "\n".join(
        [
            "Review the implementation for the current commit unit and make focused fixes if needed.",
            "Do not create a git commit; Codex Flow will commit after your review.",
            "",
            "Return exactly one final line in one of these forms:",
            f'COMMIT_UNIT_READY title="{input_data.unit.title}" summary="..."',
            'COMMIT_UNIT_NEEDS_WORK reason="..."',
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
