from __future__ import annotations

from codex_flow import state


def test_init_creates_default_state(tmp_path):
    flow = state.ensure_initialized(tmp_path)

    assert flow.root.exists()
    assert flow.tickets.exists()
    assert flow.plans.exists()
    assert flow.briefs.exists()
    assert flow.locks.exists()
    assert flow.logs.exists()
    assert flow.config.exists()
    assert flow.inbox.exists()
    assert flow.dashboard.exists()


def test_dashboard_summary_starts_empty(tmp_path):
    summary = state.dashboard_summary(tmp_path)

    assert summary["tickets"] == 0
    assert summary["plans"] == 0
    assert summary["ready_units"] == 0

