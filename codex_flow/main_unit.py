from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping

from . import plans, state
from .attempt_ledger import AttemptLedger, LedgerConflict
from .git_ops import (
    binary_patch_digest,
    changed_paths_between,
    commit_message,
    commit_parent,
    commit_paths,
    commit_tree,
    dirty_paths,
    head_sha,
    out_of_scope_diff_digest,
    path_allowed,
    scoped_diff_digest,
    stage_paths,
    status,
    worktree_patch_digest,
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
    attempt: int = 1


@dataclass(frozen=True)
class MainUnitResult:
    unit_id: str
    ledger_path: Path
    ledger_revision: int
    status: str
    changed_paths: tuple[str, ...] = ()
    commit: str = ""


def begin_main_unit(
    plan_path: str | Path,
    *,
    unit_id: str | None = None,
    retry_held: bool = False,
) -> MainUnitContract:
    plan_dir, queue = plans.load_queue(plan_path)
    unit = _select_unit(queue, unit_id)
    ledger_path = _current_ledger_path(plan_dir, unit)
    snapshot = AttemptLedger(ledger_path).load()
    if snapshot.record.get("owner") == "main":
        ledger_status = snapshot.record.get("status")
        if ledger_status in {
            "open",
            "committing",
            "completed",
        }:
            if ledger_status == "completed":
                reconciled = _reconcile_completed(plan_dir, queue, unit, ledger_path, snapshot)
            else:
                reconciled = _reconcile_active(plan_dir, queue, unit, ledger_path, snapshot)
            if reconciled:
                _append_reconciled_event(AttemptLedger(ledger_path), unit, snapshot, ledger_status)
            return _contract_from_snapshot(ledger_path, snapshot)
        if ledger_status == "held":
            if _reconcile_held(plan_dir, queue, unit, ledger_path, snapshot):
                _append_reconciled_event(AttemptLedger(ledger_path), unit, snapshot, "held")
    if unit.get("status") not in {"ready", "prompted", "needs_work"}:
        raise MainUnitError(f"unit {unit['id']} is already {unit.get('status')}")
    if unit.get("status") == "needs_work" and not retry_held:
        raise MainUnitError("held main unit requires explicit --retry-held")

    context = plans.execution_context_for_plan(plan_dir, queue)
    repo = context.execution_repo
    allowed_paths = tuple(str(path) for path in unit.get("allowed_paths", []) if str(path).strip())
    if not allowed_paths:
        raise MainUnitError("main unit requires explicit allowed_paths")
    initial_scoped = scoped_diff_digest(repo, list(allowed_paths))
    attempt = int(unit.get("main_unit_attempt") or 0) + 1
    if unit.get("status") != "needs_work" and initial_scoped:
        raise MainUnitError("allowed paths are already dirty; preserve or commit them before opening a main unit")

    if unit.get("status") == "needs_work":
        previous = AttemptLedger(ledger_path).load()
        if previous.record.get("owner") != "main" or previous.record.get("status") != "held":
            raise MainUnitError("retry requires a held main-unit ledger")
        if previous.record.get("failure_class") == "repairable_new_scope":
            raise MainUnitError("repairable_new_scope requires replanning; it cannot retry in place")
        if initial_scoped != previous.record.get("candidate_diff_sha256"):
            raise MainUnitError("held candidate changed; preserve it or replan before retry")
        current_out_of_scope = out_of_scope_diff_digest(repo, list(allowed_paths))
        if current_out_of_scope != previous.record.get("initial_out_of_scope_diff_digest", ""):
            raise MainUnitError("out-of-scope baseline changed; retry refused")
        if head_sha(repo) != previous.record.get("expected_head"):
            raise MainUnitError("HEAD changed since the held attempt; retry refused")
        ledger_path = _ledger_path(plan_dir, unit["id"], attempt)

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
                "attempt": attempt,
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
            "main_unit_attempt": attempt,
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
            "event": "main_unit_retry_opened" if attempt > 1 else "main_unit_opened",
            "ledger_revision": opened.revision,
            "queue_revision": queue_revision,
            "expected_head": expected_head,
            "attempt": attempt,
        }
    )
    return MainUnitContract(
        unit_id=unit["id"],
        ledger_path=ledger_path,
        ledger_revision=opened.revision,
        expected_head=expected_head,
        allowed_paths=allowed_paths,
        queue_revision=queue_revision,
        attempt=attempt,
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
    ledger_path = _current_ledger_path(plan_dir, unit)
    ledger = AttemptLedger(ledger_path)
    snapshot = _require_completable(ledger, expected_revision, unit_id)
    record = snapshot.record
    repo = plans.execution_context_for_plan(plan_dir, queue).execution_repo
    if record.get("status") == "completed":
        _reconcile_completed(plan_dir, queue, unit, ledger_path, snapshot)
        return _result_from_completed(ledger_path, snapshot)
    if record.get("status") == "committing":
        if _text_digest(message) != record.get("message_sha256"):
            raise MainUnitError("commit message does not match the prepared transaction")
        return _finish_committing(plan_dir, queue, unit, ledger, snapshot, repo, message)

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
        candidate_diff = worktree_patch_digest(repo, expected_head, list(changed))
        committing = ledger.compare_and_set(
            expected_revision,
            {
                **record,
                "status": "committing",
                "expected_parent": expected_head,
                "candidate_diff_sha256": candidate_diff,
                "changed_paths": list(changed),
                "changed_paths_sha256": _paths_digest(changed),
                "changed_path_count": len(changed),
                "evidence_sha256": evidence_digest,
                "message_sha256": _text_digest(message),
            },
        )
    except SystemExit as exc:
        raise MainUnitError(str(exc)) from exc
    except LedgerConflict as exc:
        raise MainUnitError(str(exc)) from exc
    ledger.append_event(
        {
            "unit_id": unit_id,
            "phase": "main",
            "event": "main_unit_commit_prepared",
            "ledger_revision": committing.revision,
            "queue_revision": record.get("queue_revision"),
            "expected_head": expected_head,
            "candidate_diff_sha256": candidate_diff,
            "changed_paths_sha256": _paths_digest(changed),
            "changed_path_count": len(changed),
            "evidence_sha256": evidence_digest,
            "message_sha256": _text_digest(message),
        }
    )
    return _finish_committing(plan_dir, queue, unit, ledger, committing, repo, message)


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
    ledger_path = _current_ledger_path(plan_dir, unit)
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


def _ledger_path(plan_dir: Path, unit_id: str, attempt: int = 1) -> Path:
    base = plan_dir / "attempts" / unit_id / "main"
    if attempt <= 1:
        return base / "attempt-ledger.json"
    return base / f"attempt-{attempt}" / "attempt-ledger.json"


def _current_ledger_path(plan_dir: Path, unit: Mapping) -> Path:
    relative = str(unit.get("main_unit_ledger") or "")
    if relative:
        return plan_dir / relative
    return _ledger_path(plan_dir, str(unit["id"]), int(unit.get("main_unit_attempt") or 1))


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


def _require_completable(ledger: AttemptLedger, expected_revision: int, unit_id: str):
    snapshot = ledger.load()
    if snapshot.revision != expected_revision:
        raise MainUnitError(
            f"main unit ledger revision changed: expected {expected_revision}, observed {snapshot.revision}"
        )
    if snapshot.record.get("unit_id") != unit_id or snapshot.record.get("owner") != "main":
        raise MainUnitError("main unit owner or unit identity mismatch")
    if snapshot.record.get("status") not in {"open", "committing", "completed"}:
        raise MainUnitError(f"main unit is already {snapshot.record.get('status')}")
    return snapshot


def _finish_committing(
    plan_dir: Path,
    queue: dict,
    unit: dict,
    ledger: AttemptLedger,
    snapshot,
    repo: Path,
    message: str,
) -> MainUnitResult:
    record = snapshot.record
    expected_parent = str(record["expected_parent"])
    changed = tuple(str(path) for path in record.get("changed_paths", []))
    observed_head = head_sha(repo)
    try:
        if observed_head == expected_parent:
            current_out_of_scope = out_of_scope_diff_digest(repo, list(record.get("allowed_paths", [])))
            if current_out_of_scope != record.get("initial_out_of_scope_diff_digest", ""):
                return _hold_committing_protocol(
                    plan_dir, queue, unit, ledger, snapshot, repo, "out_of_scope_drift"
                )
            stage_paths(repo, list(changed))
            current_patch = binary_patch_digest(repo, expected_parent, list(changed), cached=True)
            if current_patch != record.get("candidate_diff_sha256"):
                return _hold_committing_protocol(
                    plan_dir, queue, unit, ledger, snapshot, repo, "candidate_drift"
                )
            commit_paths(repo, list(changed), message)
            observed_head = head_sha(repo)
        mismatch = _commit_mismatch(repo, observed_head, record)
    except SystemExit as exc:
        raise MainUnitError(str(exc)) from exc
    if mismatch:
        return _hold_committing_protocol(plan_dir, queue, unit, ledger, snapshot, repo, mismatch)

    try:
        completed = ledger.compare_and_set(
            snapshot.revision,
            {
                **record,
                "status": "completed",
                "observed_head": observed_head,
                "commit": observed_head,
                "commit_tree": commit_tree(repo, observed_head),
            },
        )
    except LedgerConflict as exc:
        raise MainUnitError(str(exc)) from exc
    _reconcile_completed(plan_dir, queue, unit, ledger.path, completed)
    _append_plan_log(plan_dir, f"Completed commit unit {unit.get('number') or unit['id']}.")
    ledger.append_event(
        {
            "unit_id": unit["id"],
            "phase": "main",
            "event": "main_unit_completed",
            "ledger_revision": completed.revision,
            "queue_revision": record.get("queue_revision"),
            "expected_head": expected_parent,
            "observed_head": observed_head,
            "commit_sha": observed_head,
            "commit_tree": completed.record["commit_tree"],
        }
    )
    return _result_from_completed(ledger.path, completed)


def _commit_mismatch(repo: Path, observed_head: str, record: Mapping) -> str:
    expected_parent = str(record["expected_parent"])
    changed = tuple(str(path) for path in record.get("changed_paths", []))
    if commit_parent(repo, observed_head) != expected_parent:
        return "ambiguous_parent"
    observed_paths = changed_paths_between(repo, expected_parent, observed_head)
    if observed_paths != changed or _paths_digest(observed_paths) != record.get("changed_paths_sha256"):
        return "ambiguous_paths"
    observed_patch = binary_patch_digest(repo, expected_parent, list(changed), head=observed_head)
    if observed_patch != record.get("candidate_diff_sha256"):
        return "ambiguous_content"
    if _text_digest(commit_message(repo, observed_head)) != record.get("message_sha256"):
        return "ambiguous_message"
    return ""


def _hold_committing_protocol(
    plan_dir: Path,
    queue: dict,
    unit: dict,
    ledger: AttemptLedger,
    snapshot,
    repo: Path,
    reason: str,
) -> MainUnitResult:
    held = ledger.compare_and_set(
        snapshot.revision,
        {
            **snapshot.record,
            "status": "held",
            "failure_class": "protocol",
            "reason_sha256": _text_digest(reason),
            "observed_head": head_sha(repo),
        },
    )
    unit.update(
        {
            "status": "needs_work",
            "execution_owner": "main",
            "recovery_owner": "main",
            "failure_class": "protocol",
            "main_unit_ledger_revision": held.revision,
            "updated_at": state.timestamp(),
        }
    )
    plans.save_queue(plan_dir, queue)
    ledger.append_event(
        {
            "unit_id": unit["id"],
            "phase": "recovery",
            "event": "main_unit_protocol_hold",
            "ledger_revision": held.revision,
            "queue_revision": snapshot.record.get("queue_revision"),
            "reason": reason,
            "expected_head": snapshot.record.get("expected_parent"),
            "observed_head": head_sha(repo),
        }
    )
    raise MainUnitError(f"ambiguous commit state held for recovery: {reason}")


def _reconcile_active(plan_dir: Path, queue: dict, unit: dict, ledger_path: Path, snapshot) -> bool:
    record = snapshot.record
    expected = {
        "status": "in_progress",
        "execution_owner": "main",
        "main_unit_ledger": str(ledger_path.relative_to(plan_dir)),
        "main_unit_ledger_revision": snapshot.revision,
        "main_unit_queue_revision": int(record.get("queue_revision") or 0),
        "main_unit_attempt": int(record.get("attempt") or 1),
    }
    if all(unit.get(key) == value for key, value in expected.items()):
        return False
    unit.update({**expected, "updated_at": state.timestamp()})
    queue["main_unit_revision"] = max(
        int(queue.get("main_unit_revision") or 0), expected["main_unit_queue_revision"]
    )
    plans.save_queue(plan_dir, queue)
    return True


def _reconcile_held(plan_dir: Path, queue: dict, unit: dict, ledger_path: Path, snapshot) -> bool:
    record = snapshot.record
    expected = {
        "status": "needs_work",
        "execution_owner": "main",
        "recovery_owner": "main",
        "failure_class": str(record.get("failure_class") or "protocol"),
        "main_unit_ledger": str(ledger_path.relative_to(plan_dir)),
        "main_unit_ledger_revision": snapshot.revision,
        "main_unit_attempt": int(record.get("attempt") or unit.get("main_unit_attempt") or 1),
    }
    if all(unit.get(key) == value for key, value in expected.items()):
        return False
    unit.update({**expected, "updated_at": state.timestamp()})
    plans.save_queue(plan_dir, queue)
    return True


def _reconcile_completed(plan_dir: Path, queue: dict, unit: dict, ledger_path: Path, snapshot) -> bool:
    record = snapshot.record
    expected = {
        "status": "done",
        "execution_owner": "main",
        "commit": str(record.get("commit") or record.get("observed_head") or ""),
        "changed_paths": [str(path) for path in record.get("changed_paths", [])],
        "verification_evidence_sha256": str(record.get("evidence_sha256") or ""),
        "main_unit_ledger": str(ledger_path.relative_to(plan_dir)),
        "main_unit_ledger_revision": snapshot.revision,
        "main_unit_attempt": int(record.get("attempt") or unit.get("main_unit_attempt") or 1),
    }
    if all(unit.get(key) == value for key, value in expected.items()):
        return False
    unit.update({**expected, "updated_at": state.timestamp()})
    plans.save_queue(plan_dir, queue)
    return True


def _append_reconciled_event(ledger: AttemptLedger, unit: Mapping, snapshot, ledger_status: str) -> None:
    ledger.append_event(
        {
            "unit_id": unit["id"],
            "phase": "recovery",
            "event": "main_unit_reconciled",
            "ledger_revision": snapshot.revision,
            "queue_revision": snapshot.record.get("queue_revision"),
            "reason": ledger_status,
            "expected_head": snapshot.record.get("expected_head"),
            "observed_head": snapshot.record.get("observed_head"),
            "attempt": snapshot.record.get("attempt"),
        }
    )


def _result_from_completed(ledger_path: Path, snapshot) -> MainUnitResult:
    record = snapshot.record
    return MainUnitResult(
        str(record["unit_id"]),
        ledger_path,
        snapshot.revision,
        "completed",
        tuple(str(path) for path in record.get("changed_paths", [])),
        str(record.get("commit") or record.get("observed_head") or ""),
    )


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


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _contract_from_snapshot(ledger_path: Path, snapshot) -> MainUnitContract:
    record = snapshot.record
    return MainUnitContract(
        unit_id=str(record["unit_id"]),
        ledger_path=ledger_path,
        ledger_revision=snapshot.revision,
        expected_head=str(record["expected_head"]),
        allowed_paths=tuple(str(path) for path in record.get("allowed_paths", [])),
        queue_revision=int(record.get("queue_revision") or 0),
        status=str(record.get("status") or "open"),
        attempt=int(record.get("attempt") or 1),
    )


def _append_plan_log(plan_dir: Path, message: str) -> None:
    path = plan_dir / "log.md"
    current = path.read_text(encoding="utf-8") if path.exists() else "# Log\n"
    path.write_text(current.rstrip() + f"\n- {state.timestamp()} {message}\n", encoding="utf-8")
