from __future__ import annotations

from dataclasses import replace

from .codex_cli import parse_key_values
from .git_ops import path_allowed
from .implementer_agent import CommitUnitReview, POST_UNIT_REVIEW_SKILL


REQUIRED_REVIEW_GATE_FIELDS = {"status", "blockers", "important", "minor", "reason"}


def required_review_skills(required_skills: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*required_skills, POST_UNIT_REVIEW_SKILL)))


def require_post_unit_review(review: CommitUnitReview, review_message: str) -> CommitUnitReview:
    if review.status != "ready":
        return review
    gate_lines = [
        line.strip() for line in review_message.splitlines() if line.strip().startswith("REVIEW_GATE")
    ]
    if review.gate is None or len(gate_lines) != 1:
        return review_protocol_failure(
            review,
            f"missing mandatory {POST_UNIT_REVIEW_SKILL} evidence: expected exactly one REVIEW_GATE line",
        )
    values = parse_key_values(gate_lines[0])
    missing = sorted(REQUIRED_REVIEW_GATE_FIELDS - values.keys())
    invalid_counts = [
        field
        for field in ("blockers", "important", "minor")
        if not values.get(field, "").isdigit()
    ]
    if missing or invalid_counts or not values.get("reason", "").strip():
        details = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if invalid_counts:
            details.append(f"invalid counts: {', '.join(invalid_counts)}")
        if "reason" not in missing and not values.get("reason", "").strip():
            details.append("empty reason")
        return review_protocol_failure(
            review,
            f"incomplete {POST_UNIT_REVIEW_SKILL} evidence: {'; '.join(details)}",
        )
    return review


def review_protocol_failure(review: CommitUnitReview, reason: str) -> CommitUnitReview:
    return replace(
        review,
        status="needs_work",
        title="",
        summary="",
        reason=reason,
        retryable=False,
    )


def out_of_scope_paths(changed_paths: list[str], allowed_paths: list[str]) -> list[str]:
    return [path for path in changed_paths if not path_allowed(path, allowed_paths)]
