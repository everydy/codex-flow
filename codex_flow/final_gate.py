from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
from datetime import datetime, timezone

from .execution_policy import ExecutionMode, ExecutionProfile, POLICY_VERSION


class FinalGateError(RuntimeError):
    pass


@dataclass(frozen=True)
class FinalGateRecord:
    head: str
    review_passed: bool
    verification_passed: bool
    policy_version: str = POLICY_VERSION
    created_at: str = ""

    @property
    def passed(self) -> bool:
        return self.review_passed and self.verification_passed


def select_mode(profile: ExecutionProfile, *, interactive: bool) -> ExecutionMode:
    if profile is ExecutionProfile.DOCS_ONLY and interactive:
        return ExecutionMode.PARENT_DIRECT
    return ExecutionMode.ISOLATED_CHILD


def write_final_gate(path: str | Path, record: FinalGateRecord) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(record)
    if not payload["created_at"]:
        payload["created_at"] = datetime.now(timezone.utc).isoformat()
    target.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return target


def require_final_gate(path: str | Path, *, expected_head: str) -> FinalGateRecord:
    target = Path(path)
    if not target.exists():
        raise FinalGateError("final gate evidence is missing")
    value = json.loads(target.read_text(encoding="utf-8"))
    record = FinalGateRecord(**value)
    if record.head != expected_head:
        raise FinalGateError(f"final gate HEAD is stale: {record.head} != {expected_head}")
    if record.policy_version != POLICY_VERSION:
        raise FinalGateError("final gate policy version is stale")
    if not record.passed:
        raise FinalGateError("final gate review or verification failed")
    return record
