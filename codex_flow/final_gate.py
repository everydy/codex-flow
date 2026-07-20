from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping

from . import plans
from .execution_policy import ExecutionMode, ExecutionProfile, POLICY_VERSION
from .git_ops import head_sha


class FinalGateError(RuntimeError):
    pass


@dataclass(frozen=True)
class FinalGateRecord:
    reviewed_head: str
    plan_digest: str
    terminal_queue_revision: str
    cumulative_review_status: str
    canonical_test_status: str
    evidence_hash: str
    policy_version: str = POLICY_VERSION
    created_at: str = ""

    @property
    def passed(self) -> bool:
        return self.cumulative_review_status == "pass" and self.canonical_test_status == "pass"


@dataclass(frozen=True)
class FinalizeGuard:
    record: FinalGateRecord

    @classmethod
    def require(cls, plan_path: str | Path) -> "FinalizeGuard":
        plan_dir, queue = _load_stable_queue(plan_path)
        repo = plans.execution_context_for_plan(plan_dir, queue).execution_repo
        record = require_final_gate(
            plan_dir / "final-gate.json",
            expected_head=head_sha(repo),
            expected_plan_digest=plan_file_digest(plan_dir / "plan.md"),
            expected_queue_revision=terminal_queue_revision(queue),
        )
        return cls(record)


def select_mode(profile: ExecutionProfile, *, interactive: bool) -> ExecutionMode:
    if profile is ExecutionProfile.DOCS_ONLY and interactive:
        return ExecutionMode.PARENT_DIRECT
    return ExecutionMode.ISOLATED_CHILD


def produce_final_gate(
    plan_path: str | Path,
    *,
    review_evidence: Mapping,
    test_evidence: Mapping,
) -> FinalGateRecord:
    plan_dir, queue = _load_stable_queue(plan_path)
    unfinished = [unit.get("id") for unit in queue.get("units", []) if unit.get("status") != "done"]
    if unfinished:
        raise FinalGateError(f"final gate requires terminal queue; unfinished: {', '.join(map(str, unfinished))}")
    review_status = str(review_evidence.get("status") or "")
    test_status = str(test_evidence.get("status") or "")
    if review_status != "pass" or test_status != "pass":
        raise FinalGateError("final gate review and canonical tests must both pass")
    repo = plans.execution_context_for_plan(plan_dir, queue).execution_repo
    evidence_hash = hashlib.sha256(
        json.dumps(
            {"review": dict(review_evidence), "tests": dict(test_evidence)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    record = FinalGateRecord(
        reviewed_head=head_sha(repo),
        plan_digest=plan_file_digest(plan_dir / "plan.md"),
        terminal_queue_revision=terminal_queue_revision(queue),
        cumulative_review_status=review_status,
        canonical_test_status=test_status,
        evidence_hash=evidence_hash,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    write_final_gate(plan_dir / "final-gate.json", record)
    return record


def write_final_gate(path: str | Path, record: FinalGateRecord) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(record)
    if not payload["created_at"]:
        payload["created_at"] = datetime.now(timezone.utc).isoformat()
    encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def require_final_gate(
    path: str | Path,
    *,
    expected_head: str,
    expected_plan_digest: str | None = None,
    expected_queue_revision: str | None = None,
) -> FinalGateRecord:
    target = Path(path)
    if not target.exists():
        raise FinalGateError("final gate evidence is missing")
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
        record = FinalGateRecord(**value)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise FinalGateError("final gate evidence is corrupt") from exc
    if record.reviewed_head != expected_head:
        raise FinalGateError(f"final gate HEAD is stale: {record.reviewed_head} != {expected_head}")
    if expected_plan_digest is not None and record.plan_digest != expected_plan_digest:
        raise FinalGateError("final gate plan digest is stale")
    if expected_queue_revision is not None and record.terminal_queue_revision != expected_queue_revision:
        raise FinalGateError("final gate queue revision is stale")
    if record.policy_version != POLICY_VERSION:
        raise FinalGateError("final gate policy version is stale")
    if not record.passed:
        raise FinalGateError("final gate review or verification failed")
    return record


def plan_file_digest(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def terminal_queue_revision(queue: Mapping) -> str:
    units = [
        {
            "id": unit.get("id"),
            "status": unit.get("status"),
            "commit": unit.get("commit", ""),
            "changed_paths": sorted(str(path) for path in unit.get("changed_paths", [])),
        }
        for unit in queue.get("units", [])
        if isinstance(unit, Mapping)
    ]
    return hashlib.sha256(
        json.dumps(units, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load_stable_queue(plan_path: str | Path) -> tuple[Path, dict]:
    plan = Path(plan_path).expanduser().resolve()
    plan_file = plan if plan.name == "plan.md" else plan / "plan.md"
    last_digest = ""
    for attempt in range(3):
        before = plan_file_digest(plan_file) if plan_file.exists() else ""
        plan_dir, queue = plans.load_queue(plan_file)
        after = plan_file_digest(plan_file)
        if attempt > 0 and (after == before or after == last_digest):
            return plan_dir, queue
        last_digest = after
    raise FinalGateError("plan normalization did not reach a stable digest")
