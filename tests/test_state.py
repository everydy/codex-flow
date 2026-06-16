from __future__ import annotations

import json

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


def test_repo_for_plan_uses_execution_repo_metadata(tmp_path):
    execution_repo = tmp_path / "execution"
    execution_repo.mkdir()
    plan_dir = tmp_path / "source" / ".codex-flow" / "plans" / "example"
    plan_dir.mkdir(parents=True)
    (plan_dir / "queue.json").write_text(
        json.dumps({"execution_repo": str(execution_repo)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    assert state.repo_for_plan(plan_dir) == execution_repo.resolve()


def test_repo_for_plan_keeps_legacy_parent_fallback(tmp_path):
    repo = tmp_path / "repo"
    plan_dir = repo / ".codex-flow" / "plans" / "legacy"
    plan_dir.mkdir(parents=True)

    assert state.repo_for_plan(plan_dir) == repo.resolve()


def test_write_plan_metadata_merges_queue_json(tmp_path):
    plan_dir = tmp_path / ".codex-flow" / "plans" / "example"
    plan_dir.mkdir(parents=True)
    (plan_dir / "queue.json").write_text(
        json.dumps({"plan_slug": "example"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    queue = state.write_plan_metadata(plan_dir, {"execution_repo": str(tmp_path), "source_plan_sha256": "abc"})

    assert queue["plan_slug"] == "example"
    assert queue["execution_repo"] == str(tmp_path)
    assert queue["source_plan_sha256"] == "abc"
    persisted = json.loads((plan_dir / "queue.json").read_text(encoding="utf-8"))
    assert persisted == queue


def test_source_plan_path_for_plan_resolves_relative_source_repo_path(tmp_path):
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    plan_dir = tmp_path / "execution" / ".codex-flow" / "plans" / "example"
    plan_dir.mkdir(parents=True)
    (plan_dir / "source.json").write_text(
        json.dumps(
            {
                "source_repo": str(source_repo),
                "source_plan_path": "docs/plans/example.md",
                "source_plan_sha256": "abc",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    assert state.source_plan_path_for_plan(plan_dir) == (source_repo / "docs" / "plans" / "example.md").resolve()
