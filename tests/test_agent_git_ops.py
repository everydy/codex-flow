from __future__ import annotations

from codex_flow.agent import parse_agent_decision
from codex_flow.git_ops import changed_paths_since, parse_status


def test_parse_agent_decision_ready():
    decision = parse_agent_decision('notes\nCOMMIT_UNIT_READY title="Do thing" summary="done"\n')

    assert decision.status == "ready"
    assert decision.title == "Do thing"
    assert decision.summary == "done"


def test_parse_agent_decision_needs_work():
    decision = parse_agent_decision('COMMIT_UNIT_NEEDS_WORK reason="tests failed"\n')

    assert decision.status == "needs_work"
    assert decision.reason == "tests failed"


def test_changed_paths_since_ignores_codex_flow():
    before = parse_status("?? .codex-flow/inbox.md\n")
    after = parse_status("?? .codex-flow/inbox.md\n?? src/app.py\n")

    assert changed_paths_since(before, after) == ["src/app.py"]
