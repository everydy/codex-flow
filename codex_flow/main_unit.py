from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping

from . import plans, state
from .attempt_ledger import AttemptLedger, LedgerConflict
from .git_ops import (
    commit_paths,
    dirty_paths,
    head_sha,
    out_of_scope_diff_digest,
    path_allowed,
    scoped_diff_digest,
    status,
)


FAILURE_CLASSES = {
    "repairable_in_scope",
    "repairable_new_scope",
    "environment",
    "operator",
    "protocol",
}


class MainUnitError(RuntimeError):
    pass


@dataclass(frozen=True)
class MainUnitContract:
    unit_id: str
    ledger_path: Path
    ledger_revision: int
    expected_head: str
    allowed_paths: tuple[str, ...]
    queue_revision: int
    status: str = "open"


@dataclass(frozen=True)
class MainUnitResult:
    unit_id: str
    ledger_path: Path
    ledger_revision: int
    status: str
    changed_paths: tuple[str, ...] = ()
    commit: str = ""


def begin_main_unit(plan_path: str | Path, *, unit_id: str | None = None) -> MainUnitContract:
    plan_dir, queue = plans.load_queue(plan_path)
    unit = _select_unit(queue, unit_id)
    ledger_path = _ledger_path(plan_dir, unit["id"])
    if unit.get("status") == "in_progress":
        snapshot = AttemptLedger(ledger_path).load()
        if snapshot.record.get("owner") == "main" and snapshot.record.get("status") == "open":
            return _contract_from_snapshot(ledger_path, snapshot)
    if unit.get("status") not in {"ready", "prompted", "needs_work"}:
        raise MainUnitError(f"unit {unit['id']} is already {unit.get('status')}")

    context = plans.execution_context_for_plan(plan_dir, queue)
    repo = context.execution_repo
    allowed_paths = tuple(str(path) for path in unit.get("allowed_paths", []) if str(path).strip())
    if not allowed_paths:
        raise MainUnitError("main unit requires explicit allowed_paths")
    initial_scoped = scoped_diff_digest(repo, list(allowed_paths))
    if initial_scoped:
        raise MainUnitError("allowed paths are already dirty; preserve or commit them before opening a main unit")

    ledger = AttemptLedger(ledger_path)
    existing = ledger.load()
    if existing.revision:
        raise MainUnitError(f"unit {unit['id']} already has a main transaction")

    queue_revision = int(queue.get("main_unit_revision") or 0) + 1
    expected_head = head_sha(repo)
    initial_out_of_scope = out_of_scope_diff_digest(repo, list(allowed_paths))
    try:
        opened = ledger.compare_and_set(
            0,
            {
                "owner": "main",
                "status": "open",
                "unit_id": unit["id"],
                "expected_head": expected_head,
                "allowed_paths": list(allowed_paths),
                "initial_scoped_diff_digest": initial_scoped,
                "initial_out_of_scope_diff_digest": initial_out_of_scope,
                "queue_revision": queue_revision,
            },
        )
    except LedgerConflict as exc:
        raise MainUnitError(str(exc)) from exc

    unit.update(
        {
            "status": "in_progress",
            "execution_owner": "main",
            "main_unit_ledger": str(ledger_path.relative_to(plan_dir)),
            "main_unit_ledger_revision": opened.revision,
            "main_unit_queue_revision": queue_revision,
            "updated_at": state.timestamp(),
        }
    )
    queue["main_unit_revision"] = queue_revision
    plans.save_queue(plan_dir, queue)
    _append_plan_log(plan_dir, f"Started main commit unit {unit.get('number') or unit['id']}.")
    ledger.append_event(
        {
            "unit_id": unit["id"],
            "phase": "main",
            "event": "main_unit_opened",
            "ledger_revision": opened.revision,
            "queue_revision": queue_revision,
            "expected_head": expected_head,
        }
    )
    return MainUnitContract(
        unit_id=unit["id"],
        ledger_path=ledger_path,
        ledger_revision=opened.revision,
        expected_head=expected_head,
        allowed_paths=allowed_paths,
        queue_revision=queue_revision,
    )


def complete_main_unit(
    plan_path: str | Path,
    *,
    unit_id: str,
    expected_revision: int,
    evidence: Mapping,
    message: str,
) -> MainUnitResult:
    plan_dir, queue = plans.load_queue(plan_path)
    unit = _select_unit(queue, unit_id)
    ledger_path = _ledger_path(plan_dir, unit_id)
    ledger = AttemptLedger(ledger_path)
    snapshot = _require_open(ledger, expected_revision, unit_id)
    record = snapshot.record
    repo = plans.execution_context_for_plan(plan_dir, queue).execution_repo
    expected_head = str(record["expected_head"])
    observed_head = head_sha(repo)
    if observed_head != expected_head:
        _reject(ledger, unit_id, expected_revision, "head_drift", expected_head, observed_head)
        raise MainUnitError(f"HEAD changed: expected {expected_head}, observed {observed_head}")
    if evidence.get("status") != "pass":
        raise MainUnitError("verification evidence status must be pass")

    allowed_paths = [str(path) for path in record.get("allowed_paths", [])]
    observed_out_of_scope = out_of_scope_diff_digest(repo, allowed_paths)
    if observed_out_of_scope != record.get("initial_out_of_scope_diff_digest", ""):
        _reject(ledger, unit_id, expected_revision, "out_of_scope", expected_head, observed_head)
        raise MainUnitError("out-of-scope changes detected; candidate and unrelated files were preserved")
    changed = tuple(sorted(path for path in dirty_paths(status(repo)) if path_allowed(path, allowed_paths)))
    if not changed:
        raise MainUnitError("no allowed-path changes to commit")

    evidence_digest = hashlib.sha256(
        json.dumps(dict(evidence), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    try:
        commit = commit_paths(repo, list(changed), message)
    except SystemExit as exc:
        raise MainUnitError(str(exc)) from exc
    completed = ledger.compare_and_set(
        expected_revision,
        {
            **record,
            "status": "completed",
            "observed_head": head_sha(repo),
            "changed_paths_sha256": _paths_digest(changed),
            "changed_path_count": len(changed),
            "evidence_sha256": evidence_digest,
            "commit": commit,
        },
    )
    unit.update(
        {
            "status": "done",
            "execution_owner": "main",
            "commit": commit,
            "changed_paths": list(changed),
            "verification_evidence_sha256": evidence_digest,
            "main_unit_ledger_revision": completed.revision,
            "updated_at": state.timestamp(),
        }
    )
    plans.save_queue(plan_dir, queue)
    _append_plan_log(plan_dir, f"Completed commit unit {unit.get('number') or unit_id}.")
    ledger.append_event(
        {
            "unit_id": unit_id,
            "phase": "main",
            "event": "main_unit_completed",
            "ledger_revision": completed.revision,
            "queue_revision": record.get("queue_revision"),
            "expected_head": expected_head,
            "observed_head": head_sha(repo),
        }
    )
    return MainUnitResult(unit_id, ledger_path, completed.revision, "completed", changed, commit)


def hold_main_unit(
    plan_path: str | Path,
    *,
    unit_id: str,
    expected_revision: int,
    failure_class: str,
    reason: str,
) -> MainUnitResult:
    if failure_class not in FAILURE_CLASSES:
        raise MainUnitError(f"unknown failure class: {failure_class}")
    plan_dir, queue = plans.load_queue(plan_path)
    unit = _select_unit(queue, unit_id)
    ledger_path = _ledger_path(plan_dir, unit_id)
    ledger = AttemptLedger(ledger_path)
    snapshot = _require_open(ledger, expected_revision, unit_id)
    repo = plans.execution_context_for_plan(plan_dir, queue).execution_repo
    allowed_paths = [str(path) for path in snapshot.record.get("allowed_paths", [])]
    changed = tuple(sorted(path for path in dirty_paths(status(repo)) if path_allowed(path, allowed_paths)))
    held = ledger.compare_and_set(
        expected_revision,
        {
            **snapshot.record,
            "status": "held",
            "failure_class": failure_class,
            "reason_sha256": hashlib.sha256(reason.encode("utf-8")).hexdigest(),
            "candidate_diff_sha256": scoped_diff_digest(repo, allowed_paths),
            "changed_paths_sha256": _paths_digest(changed),
            "changed_path_count": len(changed),
        },
    )
    unit.update(
        {
            "status": "needs_work",
            "execution_owner": "main",
            "recovery_owner": "main",
            "failure_class": failure_class,
            "main_unit_ledger_revision": held.revision,
            "changed_paths": list(changed),
            "updated_at": state.timestamp(),
        }
    )
    plans.save_queue(plan_dir, queue)
    _append_plan_log(
        plan_dir,
        f"Commit unit {unit.get('number') or unit_id} needs_work: held by main recovery ({failure_class}).",
    )
    ledger.append_event(
        {
            "unit_id": unit_id,
            "phase": "recovery",
            "event": "recovery_decision",
            "ledger_revision": held.revision,
            "queue_revision": snapshot.record.get("queue_revision"),
            "reason": failure_class,
        }
    )
    return MainUnitResult(unit_id, ledger_path, held.revision, "held", changed)


def _select_unit(queue: dict, unit_id: str | None) -> dict:
    units = [unit for unit in queue.get("units", []) if isinstance(unit, dict)]
    if unit_id:
        for unit in units:
            if unit.get("id") == unit_id:
                return unit
        raise MainUnitError(f"unknown unit: {unit_id}")
    for unit in units:
        if unit.get("status") in {"ready", "prompted", "needs_work", "in_progress"}:
            return unit
    raise MainUnitError("no main-executable unit")


def _ledger_path(plan_dir: Path, unit_id: str) -> Path:
    return plan_dir / "attempts" / unit_id / "main" / "attempt-ledger.json"


def _require_open(ledger: AttemptLedger, expected_revision: int, unit_id: str):
    snapshot = ledger.load()
    if snapshot.revision != expected_revision:
        raise MainUnitError(
            f"main unit ledger revision changed: expected {expected_revision}, observed {snapshot.revision}"
        )
    if snapshot.record.get("unit_id") != unit_id or snapshot.record.get("owner") != "main":
        raise MainUnitError("main unit owner or unit identity mismatch")
    if snapshot.record.get("status") != "open":
        raise MainUnitError(f"main unit is already {snapshot.record.get('status')}")
    return snapshot


def _reject(
    ledger: AttemptLedger,
    unit_id: str,
    revision: int,
    reason: str,
    expected_head: str,
    observed_head: str,
) -> None:
    ledger.append_event(
        {
            "unit_id": unit_id,
            "phase": "main",
            "event": "main_unit_rejected",
            "ledger_revision": revision,
            "reason": reason,
            "expected_head": expected_head,
            "observed_head": observed_head,
        }
    )


def _paths_digest(paths: tuple[str, ...]) -> str:
    return hashlib.sha256("\0".join(paths).encode("utf-8")).hexdigest()


def _contract_from_snapshot(ledger_path: Path, snapshot) -> MainUnitContract:
    record = snapshot.record
    return MainUnitContract(
        unit_id=str(record["unit_id"]),
        ledger_path=ledger_path,
        ledger_revision=snapshot.revision,
        expected_head=str(record["expected_head"]),
        allowed_paths=tuple(str(path) for path in record.get("allowed_paths", [])),
        queue_revision=int(record.get("queue_revision") or 0),
    )


def _append_plan_log(plan_dir: Path, message: str) -> None:
    path = plan_dir / "log.md"
    current = path.read_text(encoding="utf-8") if path.exists() else "# Log\n"
    path.write_text(current.rstrip() + f"\n- {state.timestamp()} {message}\n", encoding="utf-8")
