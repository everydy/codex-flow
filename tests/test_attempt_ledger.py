from __future__ import annotations

import json
import os
import threading

import pytest

from codex_flow.attempt_ledger import AttemptLedger, LedgerConflict


def test_ledger_compare_and_set_is_atomic_and_rejects_stale_revision(tmp_path):
    ledger = AttemptLedger(tmp_path / "ledger.json")

    first = ledger.compare_and_set(0, {"status": "running"})
    assert first.revision == 1
    assert json.loads((tmp_path / "ledger.json").read_text())["record"]["status"] == "running"

    with pytest.raises(LedgerConflict, match="expected 0, observed 1"):
        ledger.compare_and_set(0, {"status": "stale"})


def test_ledger_update_preserves_existing_fields(tmp_path):
    ledger = AttemptLedger(tmp_path / "ledger.json")
    ledger.update({"attempt_id": "a", "status": "running"})

    updated = ledger.update({"status": "pass"})

    assert updated.revision == 2
    assert updated.record == {"attempt_id": "a", "status": "pass"}


def test_event_reader_ignores_only_truncated_final_record(tmp_path):
    ledger = AttemptLedger(tmp_path / "ledger.json")
    ledger.append_event({"attempt_id": "a", "event": "heartbeat"})
    with ledger.events_path.open("ab") as handle:
        handle.write(b'{"attempt_id":"broken"')

    events, truncated = ledger.recover_events()

    assert truncated is True
    assert events == [{"attempt_id": "a", "event": "heartbeat"}]


def test_event_writer_rejects_free_form_payload(tmp_path):
    ledger = AttemptLedger(tmp_path / "ledger.json")

    with pytest.raises(ValueError, match="non-allowlisted"):
        ledger.append_event({"attempt_id": "a", "event": "heartbeat", "payload": "raw secret"})


def test_concurrent_compare_and_set_has_exactly_one_winner(tmp_path):
    path = tmp_path / "ledger.json"
    outcomes: list[str] = []

    def write(value: str) -> None:
        try:
            AttemptLedger(path).compare_and_set(0, {"winner": value})
            outcomes.append("pass")
        except LedgerConflict:
            outcomes.append("conflict")

    threads = [threading.Thread(target=write, args=(value,)) for value in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["conflict", "pass"]
    assert AttemptLedger(path).load().revision == 1


def test_atomic_replace_failure_preserves_previous_snapshot(tmp_path, monkeypatch):
    ledger = AttemptLedger(tmp_path / "ledger.json")
    ledger.update({"status": "before"})
    real_replace = os.replace

    def fail_replace(source, target):
        if target == ledger.path:
            raise OSError("simulated crash before rename")
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated crash"):
        ledger.update({"status": "after"})
    assert ledger.load().record["status"] == "before"
