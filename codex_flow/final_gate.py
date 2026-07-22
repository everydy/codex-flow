from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping

from . import plans
from .attempt_ledger import AttemptLedger
from .execution_policy import ExecutionMode, ExecutionProfile, POLICY_VERSION
from .git_ops import head_sha, run_process


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
        context = plans.execution_context_for_plan(plan_dir, queue)
        repo = context.execution_repo
        record = require_final_gate(
            plan_dir / "final-gate.json",
            expected_head=head_sha(repo),
            expected_plan_digest=plan_file_digest(plan_dir / "plan.md"),
            expected_queue_revision=terminal_queue_revision(queue, context=context),
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
    context = plans.execution_context_for_plan(plan_dir, queue)
    repo = context.execution_repo
    _require_terminal_provenance(plan_dir, queue, repo)
    identity = {
        "head": head_sha(repo),
        "plan_digest": plan_file_digest(plan_dir / "plan.md"),
        "terminal_queue_revision": terminal_queue_revision(queue, context=context),
    }
    review_status = _require_evidence_identity("review", review_evidence, identity)
    test_status = _require_evidence_identity("tests", test_evidence, identity)
    if review_status != "pass" or test_status != "pass":
        raise FinalGateError("final gate review and canonical tests must both pass")
    evidence_hash = hashlib.sha256(
        json.dumps(
            {"review": dict(review_evidence), "tests": dict(test_evidence)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    record = FinalGateRecord(
        reviewed_head=identity["head"],
        plan_digest=identity["plan_digest"],
        terminal_queue_revision=identity["terminal_queue_revision"],
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


def final_evidence_identity(plan_path: str | Path) -> dict[str, str]:
    plan_dir, queue = _load_stable_queue(plan_path)
    context = plans.execution_context_for_plan(plan_dir, queue)
    return {
        "head": head_sha(context.execution_repo),
        "plan_digest": plan_file_digest(plan_dir / "plan.md"),
        "terminal_queue_revision": terminal_queue_revision(queue, context=context),
    }


def terminal_queue_revision(queue: Mapping, *, context=None) -> str:
    execution_context = {}
    if context is not None:
        execution_context = {
            "execution_repo": str(Path(context.execution_repo).resolve()),
            "worktree_path": str(Path(context.worktree_path).resolve()),
            "branch": str(context.branch),
            "execution_mode": str(context.mode),
            "source_plan_sha256": str(context.source_plan_sha256),
        }
    units = [_terminal_unit_state(unit) for unit in queue.get("units", []) if isinstance(unit, Mapping)]
    return hashlib.sha256(
        json.dumps(
            {"execution_context": execution_context, "units": units},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _require_evidence_identity(label: str, evidence: Mapping, expected: Mapping[str, str]) -> str:
    for field, expected_value in expected.items():
        observed = str(evidence.get(field) or "")
        if not observed:
            raise FinalGateError(f"{label} evidence is missing {field}")
        if observed != expected_value:
            raise FinalGateError(f"{label} evidence {field} is stale")
    return str(evidence.get("status") or "")


def _terminal_unit_state(unit: Mapping) -> dict:
    raw_policy = unit.get("execution_policy")
    policy = raw_policy if isinstance(raw_policy, Mapping) else {}
    return {
        "id": unit.get("id"),
        "status": unit.get("status"),
        "commit": unit.get("commit", ""),
        "changed_paths": sorted(str(path) for path in unit.get("changed_paths", [])),
        "verification_evidence_sha256": unit.get("verification_evidence_sha256", ""),
        "verification": [str(item) for item in unit.get("verification", [])],
        "execution_owner": unit.get("execution_owner", ""),
        "attempt": {
            "main_unit_attempt": unit.get("main_unit_attempt", 0),
            "main_unit_ledger": unit.get("main_unit_ledger", ""),
            "main_unit_ledger_revision": unit.get("main_unit_ledger_revision", 0),
            "attempt_ledger_revision": unit.get("attempt_ledger_revision", 0),
            "repair_attempts": unit.get("repair_attempts", 0),
            "diagnostic_path": unit.get("diagnostic_path", ""),
        },
        "execution_policy": {
            key: policy.get(key)
            for key in ("effective_profile", "executor_adapter", "review_policy", "unit_gate")
        },
    }


def _require_terminal_provenance(plan_dir: Path, queue: Mapping, repo: Path) -> None:
    release_head = head_sha(repo)
    for unit in queue.get("units", []):
        if not isinstance(unit, Mapping):
            raise FinalGateError("terminal queue contains an invalid unit record")
        unit_id = str(unit.get("id") or "unknown")
        commit = str(unit.get("commit") or "")
        evidence = str(unit.get("verification_evidence_sha256") or "")
        attempt_revision = int(
            unit.get("main_unit_ledger_revision") or unit.get("attempt_ledger_revision") or 0
        )
        if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            raise FinalGateError(f"terminal unit {unit_id} is missing a full commit SHA")
        if re.fullmatch(r"[0-9a-f]{64}", evidence) is None:
            raise FinalGateError(f"terminal unit {unit_id} is missing verification evidence")
        if attempt_revision < 1:
            raise FinalGateError(f"terminal unit {unit_id} is missing final attempt revision")
        exists = run_process(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo)
        if exists.status != 0:
            raise FinalGateError(f"terminal unit {unit_id} commit is unavailable")
        ancestor = run_process(["git", "merge-base", "--is-ancestor", commit, release_head], cwd=repo)
        if ancestor.status != 0:
            raise FinalGateError(f"terminal unit {unit_id} commit is outside the release lineage")

        main_ledger = str(unit.get("main_unit_ledger") or "")
        if main_ledger:
            ledger_path = plan_dir / main_ledger
            expected_commit_field = "commit"
            expected_evidence_field = "evidence_sha256"
        else:
            attempt = int(unit.get("repair_attempts") or 0)
            ledger_path = plan_dir / "attempts" / unit_id / f"attempt-{attempt}" / "attempt-ledger.json"
            expected_commit_field = "release_commit"
            expected_evidence_field = "verification_evidence_sha256"
        try:
            ledger_path.resolve().relative_to(plan_dir.resolve())
        except ValueError as exc:
            raise FinalGateError(f"terminal unit {unit_id} ledger escapes the plan directory") from exc
        snapshot = AttemptLedger(ledger_path).load()
        if snapshot.revision != attempt_revision:
            raise FinalGateError(f"terminal unit {unit_id} ledger revision is stale")
        record = snapshot.record
        if main_ledger and (
            record.get("owner") != "main"
            or record.get("unit_id") != unit_id
            or record.get("status") != "completed"
        ):
            raise FinalGateError(f"terminal unit {unit_id} main ledger identity is invalid")
        if not main_ledger and record.get("status") != "finalized":
            raise FinalGateError(f"terminal unit {unit_id} isolated ledger is not finalized")
        if not main_ledger and record.get("release_state") not in {None, "completed"}:
            raise FinalGateError(f"terminal unit {unit_id} isolated release is not completed")
        if str(record.get(expected_commit_field) or "") != commit:
            raise FinalGateError(f"terminal unit {unit_id} ledger commit does not match")
        if str(record.get(expected_evidence_field) or "") != evidence:
            raise FinalGateError(f"terminal unit {unit_id} ledger evidence does not match")


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
