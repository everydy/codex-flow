from __future__ import annotations

import json
import os
import threading
import subprocess

import pytest

from codex_flow.attempt_ledger import AttemptLedger, LedgerConflict
from codex_flow.git_ops import out_of_scope_diff_digest


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


def test_finalize_holds_head_moving_failure_without_rollback(tmp_path):
    ledger = AttemptLedger(tmp_path / "ledger.json")
    started = ledger.update({"status": "running"})

    final = ledger.finalize_attempt(
        expected_revision=started.revision,
        expected_head="before",
        observed_head="after",
        scoped_diff_digest="diff",
        full_diff_digest="full",
        out_of_scope_diff_digest="",
        process_closed=True,
        termination_reason="process_failure",
    )

    assert final.record["status"] == "held_adoption_required"
    assert final.record["observed_head"] == "after"


def test_adopt_attempt_rejects_stale_revision_and_evidence_mismatch(tmp_path):
    ledger = AttemptLedger(tmp_path / "ledger.json")
    started = ledger.update(
        {
            "status": "held_adoption_required",
            "observed_head": "abc",
            "scoped_diff_digest": "digest",
            "full_diff_digest": "full",
            "scope_valid": True,
            "process_closed": True,
        }
    )

    with pytest.raises(LedgerConflict):
        ledger.adopt_attempt(expected_revision=0, evidence={"observed_head": "abc", "scoped_diff_digest": "digest", "full_diff_digest": "full"}, current_head="abc", current_scoped_diff_digest="digest", current_full_diff_digest="full")
    with pytest.raises(ValueError, match="observed_head"):
        ledger.adopt_attempt(expected_revision=started.revision, evidence={"observed_head": "wrong", "scoped_diff_digest": "digest", "full_diff_digest": "full"}, current_head="abc", current_scoped_diff_digest="digest", current_full_diff_digest="full")

    adopted = ledger.adopt_attempt(
        expected_revision=started.revision,
        evidence={"observed_head": "abc", "scoped_diff_digest": "digest", "full_diff_digest": "full"},
        current_head="abc",
        current_scoped_diff_digest="digest",
        current_full_diff_digest="full",
    )
    assert adopted.record["status"] == "adopted"


def test_out_of_scope_digest_changes_when_predirty_file_bytes_change(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "allowed.md").write_text("allowed\n")
    (tmp_path / "outside.py").write_text("base\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    (tmp_path / "outside.py").write_text("dirty one\n")
    before = out_of_scope_diff_digest(tmp_path, ["allowed.md"])

    (tmp_path / "outside.py").write_text("dirty two\n")
    after = out_of_scope_diff_digest(tmp_path, ["allowed.md"])

    assert before != after


def test_stale_initialized_ledger_rejects_relaunch_state(tmp_path):
    ledger = AttemptLedger(tmp_path / "ledger.json")
    ledger.start_attempt(
        expected_head="head-a",
        scoped_diff_digest="scoped-a",
        full_diff_digest="full-a",
        out_of_scope_diff_digest="outside-a",
        allowed_paths=["docs/**"],
    )

    with pytest.raises(ValueError, match="stale attempt start invariants"):
        ledger.validate_start(
            expected_head="head-b",
            scoped_diff_digest="scoped-a",
            full_diff_digest="full-a",
            out_of_scope_diff_digest="outside-a",
            allowed_paths=["docs/**"],
        )
