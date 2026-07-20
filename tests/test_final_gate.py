import pytest

from codex_flow.execution_policy import ExecutionMode, ExecutionProfile
from codex_flow.final_gate import FinalGateError, FinalGateRecord, require_final_gate, select_mode, write_final_gate


def test_docs_interactive_is_direct_but_unattended_is_isolated():
    assert select_mode(ExecutionProfile.DOCS_ONLY, interactive=True) is ExecutionMode.PARENT_DIRECT
    assert select_mode(ExecutionProfile.DOCS_ONLY, interactive=False) is ExecutionMode.ISOLATED_CHILD
    assert select_mode(ExecutionProfile.HIGH_RISK, interactive=True) is ExecutionMode.ISOLATED_CHILD


def test_final_gate_requires_fresh_exact_head_and_both_passes(tmp_path):
    path = tmp_path / "gate.json"
    write_final_gate(path, FinalGateRecord("abc", True, True))
    assert require_final_gate(path, expected_head="abc").passed
    with pytest.raises(FinalGateError, match="stale"):
        require_final_gate(path, expected_head="def")
    write_final_gate(path, FinalGateRecord("abc", True, False))
    with pytest.raises(FinalGateError, match="failed"):
        require_final_gate(path, expected_head="abc")


def test_missing_final_gate_fails_closed(tmp_path):
    with pytest.raises(FinalGateError, match="missing"):
        require_final_gate(tmp_path / "missing.json", expected_head="abc")
