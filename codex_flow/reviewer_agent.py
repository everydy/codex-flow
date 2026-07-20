from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from . import execution_policy
from .codex_cli import (
    CodexExecFailure,
    CodexExecTimeout,
    fence,
    parse_key_values,
    parse_session_id,
    run_codex_exec,
)
from .git_ops import candidate_diff_digest, head_summary, path_allowed
from .plan_readiness import CommitUnit


INTERNAL_REVIEW_GATE = "INTERNAL_REVIEW_GATE"
REQUIRED_REVIEW_GATE_FIELDS = {"status", "blockers", "important", "minor", "reason"}


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
class CommitUnitReview:
    status: str
    title: str = ""
    summary: str = ""
    reason: str = ""
    gate: ReviewGate | None = None
    retryable: bool = True
    failure_kind: str = "review_finding"


@dataclass(frozen=True)
class ReviewerAgentInput:
    repo: Path
    plan_path: Path
    plan_content: str
    unit: CommitUnit
    implementation_session_id: str
    expected_head: str
    expected_full_diff_digest: str
    excluded_candidate_paths: tuple[str, ...] = ()
    execution_policy: dict | None = None
    allowed_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewerAgentResult:
    reviewer_session_id: str
    implementation_session_id: str
    review_message: str
    review: CommitUnitReview
    expected_head: str
    observed_head: str
    expected_full_diff_digest: str
    observed_full_diff_digest: str
    review_mode: str = "fresh_read_only"

    @property
    def head_unchanged(self) -> bool:
        return self.expected_head == self.observed_head

    @property
    def full_diff_digest_unchanged(self) -> bool:
        return self.expected_full_diff_digest == self.observed_full_diff_digest


class ReviewerProcessFailure(RuntimeError):
    def __init__(
        self,
        original: CodexExecFailure | CodexExecTimeout,
        *,
        expected_head: str,
        observed_head: str,
        expected_full_diff_digest: str,
        observed_full_diff_digest: str,
    ) -> None:
        self.original = original
        self.diagnostic_dir = original.diagnostic_dir
        self.expected_head = expected_head
        self.observed_head = observed_head
        self.expected_full_diff_digest = expected_full_diff_digest
        self.observed_full_diff_digest = observed_full_diff_digest
        self.head_unchanged = expected_head == observed_head
        self.full_diff_digest_unchanged = expected_full_diff_digest == observed_full_diff_digest
        invariant = (
            "reviewer mutation detected after process failure"
            if not self.head_unchanged or not self.full_diff_digest_unchanged
            else "review process failed with repository invariants preserved"
        )
        super().__init__(f"{invariant}: {original}")


class ReviewerAgent(Protocol):
    def review(
        self,
        input_data: ReviewerAgentInput,
        diagnostic_dir: Path | None = None,
        timeout_seconds: int | None = None,
        attempt_ledger_path: Path | None = None,
        diff_probe=None,
        liveness_probe=None,
    ) -> ReviewerAgentResult:
        ...


class CodexReadOnlyReviewer:
    def __init__(
        self,
        command: str = "codex",
        extra_args: list[str] | None = None,
        child_home: str | Path | None = None,
    ) -> None:
        self.command = command
        self.extra_args = extra_args or []
        self.child_home = child_home

    def review(
        self,
        input_data: ReviewerAgentInput,
        diagnostic_dir: Path | None = None,
        timeout_seconds: int | None = None,
        attempt_ledger_path: Path | None = None,
        diff_probe=None,
        liveness_probe=None,
    ) -> ReviewerAgentResult:
        before_head = head_summary(input_data.repo) or ""
        before_digest = candidate_diff_digest(input_data.repo, input_data.excluded_candidate_paths)
        if before_head != input_data.expected_head or before_digest != input_data.expected_full_diff_digest:
            return invariant_failure_result(
                input_data,
                reason="review precondition drifted before the read-only reviewer started",
                observed_head=before_head,
                observed_full_diff_digest=before_digest,
            )

        policy = execution_policy.classify_execution_policy(
            {
                "title": input_data.unit.title,
                "content": input_data.unit.content,
                "allowed_paths": input_data.allowed_paths,
                "execution_policy": input_data.execution_policy or {},
            }
        )
        try:
            process = run_codex_exec(
                build_read_only_review_prompt(input_data),
                repo=input_data.repo,
                command=self.command,
                sandbox="read-only",
                extra_args=self.extra_args,
                timeout_seconds=timeout_seconds,
                diagnostic_dir=diagnostic_dir / "review" if diagnostic_dir else None,
                phase="review",
                child_home=self.child_home,
                execution_profile=policy.effective_profile,
                attempt_ledger_path=attempt_ledger_path,
                diff_probe=diff_probe,
                liveness_probe=liveness_probe,
            )
        except (CodexExecTimeout, CodexExecFailure) as exc:
            observed_head = head_summary(input_data.repo) or ""
            observed_digest = candidate_diff_digest(input_data.repo, input_data.excluded_candidate_paths)
            raise ReviewerProcessFailure(
                exc,
                expected_head=input_data.expected_head,
                observed_head=observed_head,
                expected_full_diff_digest=input_data.expected_full_diff_digest,
                observed_full_diff_digest=observed_digest,
            ) from exc

        observed_head = head_summary(input_data.repo) or ""
        observed_digest = candidate_diff_digest(input_data.repo, input_data.excluded_candidate_paths)
        reviewer_session_id = parse_session_id(process.stdout) or ""
        if observed_head != input_data.expected_head or observed_digest != input_data.expected_full_diff_digest:
            return invariant_failure_result(
                input_data,
                reason="read-only reviewer changed HEAD or tracked/untracked bytes",
                observed_head=observed_head,
                observed_full_diff_digest=observed_digest,
                reviewer_session_id=reviewer_session_id,
                review_message=process.final_message,
            )
        if not reviewer_session_id:
            review = protocol_failure("review protocol error: fresh reviewer did not report a session id")
        elif reviewer_session_id == input_data.implementation_session_id:
            review = protocol_failure("review protocol error: reviewer reused the current implementation session")
        else:
            review = require_internal_review(parse_commit_unit_review(process.final_message), process.final_message)
        return ReviewerAgentResult(
            reviewer_session_id=reviewer_session_id,
            implementation_session_id=input_data.implementation_session_id,
            review_message=process.final_message,
            review=review,
            expected_head=input_data.expected_head,
            observed_head=observed_head,
            expected_full_diff_digest=input_data.expected_full_diff_digest,
            observed_full_diff_digest=observed_digest,
        )


def build_read_only_review_prompt(input_data: ReviewerAgentInput) -> str:
    try:
        plan_path = str(input_data.plan_path.relative_to(input_data.repo))
    except ValueError:
        plan_path = str(input_data.plan_path)
    return "\n".join(
        [
            "You are Agent 3: Read-only Reviewer for the Codex Flow workflow orchestrator.",
            f"Read {plan_path} and review only commit unit {input_data.unit.number}.",
            "Inspect the current repository diff and report findings by severity.",
            "Do not edit, repair, create files, commit, open or merge a PR, deploy, or publish.",
            "Do not resume or reuse the implementation session.",
            "If a blocker or important finding exists, return needs_work; a later writable implementer attempt owns repairs.",
            "Do not load or invoke generic external review wrappers unless the plan explicitly declares them as required skills.",
            "",
            "Return exactly two final machine-readable lines:",
            f'{INTERNAL_REVIEW_GATE} status="pass|needs_work" blockers=0 important=0 minor=0 reason="..."',
            "Then return exactly one decision line (never both):",
            f'COMMIT_UNIT_READY title="{input_data.unit.title}" summary="..."',
            'COMMIT_UNIT_NEEDS_WORK reason="..."',
            "",
            "Selected commit unit:",
            fence(f"### Commit {input_data.unit.number}: {input_data.unit.title}\n\n{input_data.unit.content}"),
            "",
            "Full plan.md:",
            fence(input_data.plan_content),
        ]
    )


def required_review_skills(required_skills: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(required_skills))


def internal_gate_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip().split(maxsplit=1)[0] == INTERNAL_REVIEW_GATE
    ]


def parse_review_gate(text: str) -> ReviewGate | None:
    lines = internal_gate_lines(text)
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
        return protocol_failure(
            f"review protocol error: expected exactly one COMMIT_UNIT terminal line, received {len(terminal_lines)}",
            gate=gate,
        )
    line = terminal_lines[0]
    values = parse_key_values(line)
    if line.startswith("COMMIT_UNIT_NEEDS_WORK") and not values.get("reason", "").strip():
        return protocol_failure(
            "review protocol error: COMMIT_UNIT_NEEDS_WORK requires a non-empty reason",
            gate=gate,
        )
    if gate and not gate.passed:
        reason = gate.reason or (
            f"review gate failed: blockers={gate.blockers} important={gate.important} "
            f"minor={gate.minor} score={gate.score}"
        )
        return CommitUnitReview("needs_work", reason=reason, gate=gate)
    if line.startswith("COMMIT_UNIT_READY"):
        return CommitUnitReview(
            "ready",
            title=values.get("title", "Commit unit ready"),
            summary=values.get("summary", "Ready to commit."),
            gate=gate,
        )
    if line.startswith("COMMIT_UNIT_NEEDS_WORK"):
        return CommitUnitReview("needs_work", reason=values["reason"], gate=gate)
    return protocol_failure(f"review protocol error: unknown terminal line: {line}", gate=gate)


def require_internal_review(review: CommitUnitReview, review_message: str) -> CommitUnitReview:
    gate_lines = internal_gate_lines(review_message)
    if review.gate is None or len(gate_lines) != 1:
        return review_protocol_failure(
            review,
            f"missing internal review evidence: expected exactly one {INTERNAL_REVIEW_GATE} line",
        )
    values = parse_key_values(gate_lines[0])
    missing = sorted(REQUIRED_REVIEW_GATE_FIELDS - values.keys())
    raw_status = values.get("status", "").strip().lower()
    invalid_status = "status" not in missing and raw_status not in {"pass", "needs_work"}
    invalid_counts = [
        field for field in ("blockers", "important", "minor") if not values.get(field, "").isdigit()
    ]
    if missing or invalid_status or invalid_counts or not values.get("reason", "").strip():
        details = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if invalid_status:
            details.append(f"invalid status: {raw_status}")
        if invalid_counts:
            details.append(f"invalid counts: {', '.join(invalid_counts)}")
        if "reason" not in missing and not values.get("reason", "").strip():
            details.append("empty reason")
        return review_protocol_failure(review, f"incomplete internal review evidence: {'; '.join(details)}")
    return review


def protocol_failure(reason: str, *, gate: ReviewGate | None = None) -> CommitUnitReview:
    return CommitUnitReview(
        "needs_work",
        reason=reason,
        gate=gate,
        retryable=False,
        failure_kind="protocol_failure",
    )


def review_protocol_failure(review: CommitUnitReview, reason: str) -> CommitUnitReview:
    return replace(
        review,
        status="needs_work",
        title="",
        summary="",
        reason=reason,
        retryable=False,
        failure_kind="protocol_failure",
    )


def review_control_plane_exclusions(
    repo: Path,
    diagnostic_dir: Path | None,
    attempt_ledger_path: Path | None,
) -> tuple[str, ...]:
    paths: list[Path] = []
    if diagnostic_dir is not None:
        paths.append(diagnostic_dir / "review")
    if attempt_ledger_path is not None:
        paths.extend(
            (
                attempt_ledger_path,
                attempt_ledger_path.with_suffix(attempt_ledger_path.suffix + ".lock"),
                attempt_ledger_path.with_name("events.jsonl"),
            )
        )
    relative: list[str] = []
    resolved_repo = repo.resolve()
    for path in paths:
        try:
            relative.append(path.resolve().relative_to(resolved_repo).as_posix())
        except ValueError:
            continue
    return tuple(dict.fromkeys(relative))


def invariant_failure_result(
    input_data: ReviewerAgentInput,
    *,
    reason: str,
    observed_head: str,
    observed_full_diff_digest: str,
    reviewer_session_id: str = "",
    review_message: str = "",
) -> ReviewerAgentResult:
    return ReviewerAgentResult(
        reviewer_session_id=reviewer_session_id,
        implementation_session_id=input_data.implementation_session_id,
        review_message=review_message,
        review=protocol_failure(reason),
        expected_head=input_data.expected_head,
        observed_head=observed_head,
        expected_full_diff_digest=input_data.expected_full_diff_digest,
        observed_full_diff_digest=observed_full_diff_digest,
    )


def out_of_scope_paths(changed_paths: list[str], allowed_paths: list[str]) -> list[str]:
    return [path for path in changed_paths if not path_allowed(path, allowed_paths)]
