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
    config = flow.config.read_text(encoding="utf-8")
    assert "remote_pr: finalize_command" in config
    assert "remote_merge: finalize_command" in config


def test_dashboard_summary_starts_empty(tmp_path):
    summary = state.dashboard_summary(tmp_path)

    assert summary["tickets"] == 0
    assert summary["plans"] == 0
    assert summary["ready_units"] == 0
    assert not (tmp_path / ".codex-flow").exists()


def test_dashboard_summary_projects_missing_queue_without_writing_cache(tmp_path):
    plan_dir = tmp_path / ".codex-flow" / "plans" / "projection"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.md").write_text(
        "# Projection\n\nBranch: codex/projection\n\n### Commit 1: First\n\nDo it.\n",
        encoding="utf-8",
    )

    summary = state.dashboard_summary(tmp_path)

    assert summary["plans"] == 1
    assert summary["ready_units"] == 1
    assert not (plan_dir / "queue.json").exists()
