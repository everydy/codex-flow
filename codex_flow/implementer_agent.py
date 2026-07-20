from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .codex_cli import fence, parse_key_values, parse_session_id, run_codex_exec
from . import execution_policy, plan_readiness
from .plan_readiness import CommitUnit


@dataclass(frozen=True)
class CommitUnitReview:
    status: str
    title: str = ""
    summary: str = ""
    reason: str = ""
    gate: "ReviewGate | None" = None
    retryable: bool = True


@dataclass(frozen=True)
class ReviewGate:
    status: str
    blockers: int = 0
    important: int = 0
    minor: int = 0
    reason: str = ""

    @property
    def score(self) -> int:
        return 100 - (self.blockers * 50) - (self.important * 20) - (self.minor * 5)

    @property
    def passed(self) -> bool:
        return self.status == "pass" and self.blockers == 0 and self.important == 0

    def to_dict(self) -> dict[str, int | str | bool]:
        return {
            "status": self.status,
            "blockers": self.blockers,
            "important": self.important,
            "minor": self.minor,
            "score": self.score,
            "passed": self.passed,
            "reason": self.reason,
        }


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
    execution_policy: dict | None = None
    allowed_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImplementerAgentResult:
    session_id: str
    implementation_message: str
    review_message: str
    review: CommitUnitReview


class ImplementerAgent(Protocol):
    def implement(
        self,
        input_data: ImplementerAgentInput,
        diagnostic_dir: Path | None = None,
        timeout_seconds: int | None = None,
        attempt_ledger_path: Path | None = None,
        diff_probe=None,
        liveness_probe=None,
    ) -> ImplementerAgentResult:
        ...


POST_UNIT_REVIEW_SKILL = "review-all-in-one"


class CodexImplementerAgent:
    def __init__(
        self,
        command: str = "codex",
        extra_args: list[str] | None = None,
        child_home: str | Path | None = None,
    ) -> None:
        self.command = command
        self.extra_args = extra_args or []
        self.child_home = child_home

    def implement(
        self,
        input_data: ImplementerAgentInput,
        diagnostic_dir: Path | None = None,
        timeout_seconds: int | None = None,
        attempt_ledger_path: Path | None = None,
        diff_probe=None,
        liveness_probe=None,
    ) -> ImplementerAgentResult:
        policy = execution_policy.classify_execution_policy(
            {
                "title": input_data.unit.title,
                "content": input_data.unit.content,
                "allowed_paths": input_data.allowed_paths,
                "execution_policy": input_data.execution_policy or {},
            }
        )
        implementation = run_codex_exec(
            build_implementation_prompt(input_data),
            repo=input_data.repo,
            command=self.command,
            sandbox="workspace-write",
            extra_args=self.extra_args,
            timeout_seconds=timeout_seconds,
            diagnostic_dir=diagnostic_dir / "implementation" if diagnostic_dir else None,
            phase="implementation",
            child_home=self.child_home,
            execution_profile=policy.effective_profile,
            attempt_ledger_path=attempt_ledger_path,
            diff_probe=diff_probe,
            liveness_probe=liveness_probe,
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
            timeout_seconds=timeout_seconds,
            diagnostic_dir=diagnostic_dir / "review" if diagnostic_dir else None,
            phase="review",
            child_home=self.child_home,
            execution_profile=policy.effective_profile,
            attempt_ledger_path=attempt_ledger_path,
            diff_probe=diff_probe,
            liveness_probe=liveness_probe,
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
            "Before editing, load and apply every required skill named in the Skill Routing Manifest.",
            "Required skills are mandatory and were attested before this session; if any cannot be loaded, stop without editing and return needs_work.",
            "Optional skills may be skipped only with an explicit reason in the review summary.",
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
            "Use review-all-in-one as the wrapper and include review-swarm style findings by severity.",
            "Treat blocker or important findings from that review as commit blockers: fix them inside this same review pass when safe, or return `COMMIT_UNIT_NEEDS_WORK` with the reason.",
            f"If `{POST_UNIT_REVIEW_SKILL}` is unavailable, return `COMMIT_UNIT_NEEDS_WORK` and explain the missing review gate.",
            "",
            "Return exactly two final machine-readable lines:",
            'REVIEW_GATE status="pass|needs_work" blockers=0 important=0 minor=0 reason="..."',
            "Then return exactly one of the following decision lines (never both, and never an empty reason):",
            f'COMMIT_UNIT_READY title="{input_data.unit.title}" summary="..."',
            'COMMIT_UNIT_NEEDS_WORK reason="..."',
            "Do not echo the unused decision form.",
            "",
            "Commit condition: `REVIEW_GATE status=\"pass\" blockers=0 important=0`.",
            "Minor findings may pass, but include the minor count so Codex Flow can record the score.",
            "",
            "Review requirement: confirm that every required skill from the Skill Routing Manifest was applied. Required skills cannot be skipped.",
            "Optional skills may be skipped only with an explicit reason.",
        ]
    )


def parse_review_gate(text: str) -> ReviewGate | None:
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("REVIEW_GATE")]
    if not lines:
        return None
    values = parse_key_values(lines[-1])
    status = values.get("status", "needs_work").strip().lower()
    if status not in {"pass", "needs_work"}:
        status = "needs_work"
    return ReviewGate(
        status=status,
        blockers=parse_non_negative_int(values.get("blockers", "0")),
        important=parse_non_negative_int(values.get("important", "0")),
        minor=parse_non_negative_int(values.get("minor", "0")),
        reason=values.get("reason", ""),
    )


def parse_non_negative_int(value: str) -> int:
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def parse_commit_unit_review(text: str) -> CommitUnitReview:
    gate = parse_review_gate(text)
    terminal_lines = [line.strip() for line in text.splitlines() if line.strip().startswith("COMMIT_UNIT_")]
    if len(terminal_lines) != 1:
        return CommitUnitReview(
            "needs_work",
            reason=f"review protocol error: expected exactly one COMMIT_UNIT terminal line, received {len(terminal_lines)}",
            gate=gate,
            retryable=False,
        )
    line = terminal_lines[0]
    values = parse_key_values(line)
    if line.startswith("COMMIT_UNIT_NEEDS_WORK") and not values.get("reason", "").strip():
        return CommitUnitReview(
            "needs_work",
            reason="review protocol error: COMMIT_UNIT_NEEDS_WORK requires a non-empty reason",
            gate=gate,
            retryable=False,
        )
    if gate and not gate.passed:
        reason = gate.reason or f"review gate failed: blockers={gate.blockers} important={gate.important} minor={gate.minor} score={gate.score}"
        return CommitUnitReview("needs_work", reason=reason, gate=gate)
    if line.startswith("COMMIT_UNIT_READY"):
        return CommitUnitReview("ready", title=values.get("title", "Commit unit ready"), summary=values.get("summary", "Ready to commit."), gate=gate)
    if line.startswith("COMMIT_UNIT_NEEDS_WORK"):
        return CommitUnitReview("needs_work", reason=values["reason"], gate=gate)
    raise ValueError(f"Unknown commit unit review: {line}")
