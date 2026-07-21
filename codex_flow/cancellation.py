from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from . import plans, state
from .attempt_ledger import AttemptLedger


class CancellationError(RuntimeError):
    pass


def request_cancel(plan_path: str | Path, *, unit_id: str, attempt: int) -> Path:
    plan_dir, queue = plans.load_queue(plan_path)
    unit = next(
        (item for item in queue.get("units", []) if isinstance(item, dict) and item.get("id") == unit_id),
        None,
    )
    if unit is None:
        raise CancellationError(f"unknown unit: {unit_id}")
    if attempt < 0:
        raise CancellationError("attempt must be zero or greater")
    attempt_dir = plan_dir / "attempts" / unit_id / f"attempt-{attempt}"
    ledger = AttemptLedger(attempt_dir / "attempt-ledger.json").load()
    if ledger.revision < 1 or ledger.record.get("status") not in {
        "starting",
        "running",
        "implementation",
        "review",
    }:
        raise CancellationError("attempt is not currently cancellable")
    marker = attempt_dir / "cancel-request.json"
    payload = {
        "schema_version": 1,
        "unit_id": unit_id,
        "attempt": attempt,
        "observed_ledger_revision": ledger.revision,
        "requested_at": state.timestamp(),
        "reason": "preserve_changes",
    }
    marker.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=marker.parent, prefix=".cancel-request.", delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, marker)
    AttemptLedger(attempt_dir / "attempt-ledger.json").append_event(
        {
            "unit_id": unit_id,
            "phase": "operator",
            "event": "operator_cancel_requested",
            "ledger_revision": ledger.revision,
            "attempt": attempt,
            "reason": "preserve_changes",
        }
    )
    return marker


def cancellation_requested(attempt_dir: str | Path, *, unit_id: str, attempt: int) -> bool:
    marker = Path(attempt_dir) / "cancel-request.json"
    if not marker.exists():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        payload.get("schema_version") == 1
        and payload.get("unit_id") == unit_id
        and payload.get("attempt") == attempt
        and payload.get("reason") == "preserve_changes"
    )
