from __future__ import annotations

from datetime import datetime, timedelta
import json
import subprocess

import pytest

from codex_flow import cli, dashboard as dashboard_view, plans, pr, tickets
from codex_flow.attempt_ledger import AttemptLedger
from codex_flow.dashboard import render_dashboard
from codex_flow.final_gate import final_evidence_identity, produce_final_gate


def test_route_short_request_fails_instead_of_reusing_active_plan(tmp_path, capsys):
    first = tickets.submit_ticket("Improve dashboard", repo=tmp_path)
    plans.create_plan_from_ticket(first.path, repo=tmp_path)

    status = cli.main(["--repo", str(tmp_path), "route", "Improve dashboard copy", "--auto-resolve"])

    output = capsys.readouterr().out
    assert status == 1
    assert "route requires a plan-first Markdown file path" in output


def test_route_no_longer_accepts_router_or_planner_flags():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["route", "docs/plans/example.md", "--router", "codex"])

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["route", "docs/plans/example.md", "--planner", "codex"])


def test_pr_draft_and_pr_create_commands_are_distinct():
    draft_args = cli.build_parser().parse_args(["open-pr", "--plan", "plan.md"])
    create_args = cli.build_parser().parse_args(["create-pr", "--plan", "plan.md"])

    assert draft_args.command == "open-pr"
    assert draft_args.remote is False
    assert create_args.command == "create-pr"
    assert not hasattr(create_args, "remote")


def test_route_explicit_plan_no_longer_appends_request(tmp_path, capsys):
    first = tickets.submit_ticket("Plan target", repo=tmp_path)
    plan = plans.create_plan_from_ticket(first.path, repo=tmp_path)

    status = cli.main(["--repo", str(tmp_path), "route", "Attach this", "--auto-resolve"])

    output = capsys.readouterr().out
    assert status == 1
    assert "route requires a plan-first Markdown file path" in output
    requests = (plan.directory / "requests.md").read_text(encoding="utf-8")
    assert "Attach this" not in requests


def test_dashboard_renders_plan_progress_and_suggested_command(tmp_path):
    ticket = tickets.submit_ticket("Dashboard plan", repo=tmp_path)
    plans.create_plan_from_ticket(ticket.path, repo=tmp_path)

    output = render_dashboard(tmp_path)

    assert "PR lock: none" in output
    assert "Active Plans" in output
    assert "Suggested command:" in output
    assert "run-all" in output


def test_dashboard_on_fresh_repo_is_read_only(tmp_path):
    output = render_dashboard(tmp_path)

    assert "Plans: 0" in output
    assert not (tmp_path / ".codex-flow").exists()

    assert cli.main(["--repo", str(tmp_path), "status"]) == 0
    assert not (tmp_path / ".codex-flow").exists()


def test_repeated_dashboard_projection_does_not_change_state_tree(tmp_path):
    ticket = tickets.submit_ticket("Read-only dashboard", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)
    plan.queue_json.unlink()

    def snapshot():
        return {
            str(path.relative_to(tmp_path)): path.read_bytes()
            for path in sorted((tmp_path / ".codex-flow").rglob("*"))
            if path.is_file()
        }

    before = snapshot()
    first = render_dashboard(tmp_path)
    middle = snapshot()
    second = render_dashboard(tmp_path)

    assert first == second
    assert before == middle == snapshot()
    assert not plan.queue_json.exists()


def test_dashboard_watch_does_not_change_state_tree(tmp_path, monkeypatch):
    ticket = tickets.submit_ticket("Watch dashboard", repo=tmp_path)
    plans.create_plan_from_ticket(ticket.path, repo=tmp_path)
    before = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in sorted((tmp_path / ".codex-flow").rglob("*"))
        if path.is_file()
    }
    calls = 0
    real_render = dashboard_view.render_dashboard

    def bounded_render(repo):
        nonlocal calls
        calls += 1
        if calls > 2:
            raise KeyboardInterrupt
        return real_render(repo)

    monkeypatch.setattr(dashboard_view, "render_dashboard", bounded_render)
    monkeypatch.setattr("codex_flow.cli.time.sleep", lambda _seconds: None)

    assert cli.main(["--repo", str(tmp_path), "dashboard", "--watch", "--interval", "0"]) == 0
    after = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in sorted((tmp_path / ".codex-flow").rglob("*"))
        if path.is_file()
    }
    assert calls == 3
    assert before == after


def test_dashboard_projects_main_unit_status_from_queue_without_mutation(tmp_path):
    ticket = tickets.submit_ticket("Main dashboard plan", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    unit = queue["units"][0]
    unit.update(
        {
            "status": "in_progress",
            "execution_owner": "main",
            "repair_attempts": "not-a-number",
            "updated_at": (datetime.now() - timedelta(seconds=75)).replace(microsecond=0).isoformat(),
        }
    )
    plans.save_queue(plan.directory, queue)
    before = plan.queue_json.read_bytes()

    output = render_dashboard(tmp_path)

    assert "Current owner: `main`" in output
    assert "Current phase/status: `main` / `in_progress`" in output
    assert "since queue update" in output
    assert "Current verification:" in output
    assert "Status source: queue fallback (ledger not recorded)" in output
    assert plan.queue_json.read_bytes() == before


def test_dashboard_projects_isolated_ledger_heartbeat_without_mutation(tmp_path):
    ticket = tickets.submit_ticket("Isolated dashboard plan", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    unit = queue["units"][0]
    unit.update(
        {
            "status": "in_progress",
            "execution_policy": {"executor_adapter": "isolated-child"},
            "repair_attempts": 0,
        }
    )
    plans.save_queue(plan.directory, queue)
    ledger_path = plan.directory / "attempts" / unit["id"] / "attempt-0" / "attempt-ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps(
            {
                "revision": 4,
                "record": {
                    "phase": "implementation",
                    "status": "running",
                    "last_event": "heartbeat",
                    "heartbeat_elapsed_ms": 65_000,
                    "child_output_age_ms": 3_000,
                },
            }
        ),
        encoding="utf-8",
    )
    queue_before = plan.queue_json.read_bytes()
    ledger_before = ledger_path.read_bytes()

    output = render_dashboard(tmp_path)

    assert "Current owner: `isolated-child`" in output
    assert "Current phase/status: `implementation` / `running`" in output
    assert "Elapsed: 1m 5s (ledger)" in output
    assert "Last progress: heartbeat; child output age 3s" in output
    assert "Status source: attempt ledger r4" in output
    assert plan.queue_json.read_bytes() == queue_before
    assert ledger_path.read_bytes() == ledger_before


def test_dashboard_redacts_wait_reason_and_falls_back_from_corrupt_ledger(tmp_path, monkeypatch):
    ticket = tickets.submit_ticket("Held dashboard plan", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    unit = queue["units"][0]
    unit.update(
        {
            "status": "needs_work",
            "execution_owner": "main",
            "main_unit_ledger": f"attempts/{unit['id']}/main/attempt-ledger.json",
            "failure_class": "environment",
            "last_needs_work_reason": "token=super-secret-token\n" + "x" * 300,
        }
    )
    plans.save_queue(plan.directory, queue)
    ledger_path = plan.directory / unit["main_unit_ledger"]
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text("{broken", encoding="utf-8")
    (plan.directory / "log.md").write_text(
        "# Log\n- token=super-secret-token " + "y" * 300 + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_TEST_SECRET_TOKEN", "super-secret-token")

    output = render_dashboard(tmp_path)

    assert "Waiting: environment: token=[REDACTED]" in output
    assert "super-secret-token" not in output
    assert "\n- Status source: queue fallback (ledger unreadable)" in output
    waiting_line = next(line for line in output.splitlines() if line.startswith("- Waiting:"))
    assert len(waiting_line) < 220
    recent_line = next(line for line in output.splitlines() if line.startswith("- token="))
    assert "[REDACTED]" in recent_line
    assert len(recent_line) < 250


def test_set_clear_pr_lock_cli(tmp_path, capsys):
    status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "set-pr-lock",
            "--branch",
            "codex/demo",
            "--pr-url",
            "https://github.com/example/repo/pull/1",
        ]
    )
    assert status == 0
    assert "set_pr_lock:" in capsys.readouterr().out
    assert pr.read_pr_lock(tmp_path)

    status = cli.main(["--repo", str(tmp_path), "clear-pr-lock"])
    assert status == 0
    assert "removed" in capsys.readouterr().out
    assert not pr.read_pr_lock(tmp_path)


def test_pr_check_merged_clears_lock_and_drains_inbox(tmp_path, capsys):
    ticket = tickets.submit_ticket("Queued after PR", repo=tmp_path)
    pr.write_pr_lock(tmp_path, "codex/demo", "https://github.com/example/repo/pull/7", "reviewing")
    fake_gh = tmp_path / "fake_gh.py"
    fake_gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'state': 'MERGED', 'mergedAt': '2026-05-12T00:00:00Z', 'url': 'https://github.com/example/repo/pull/7'}))\n",
        encoding="utf-8",
    )
    fake_gh.chmod(fake_gh.stat().st_mode | 0o111)

    status = cli.main(["--repo", str(tmp_path), "pr-check", "--gh-command", str(fake_gh)])

    output = capsys.readouterr().out
    assert status == 0
    assert "cleared" in output
    assert "drain: empty" in output
    assert not pr.read_pr_lock(tmp_path)
    assert tickets.load_ticket(ticket.path).status == "inbox"


def test_remote_merge_success_clears_matching_pr_lock(tmp_path, monkeypatch):
    from codex_flow import merge
    from codex_flow.git_ops import ProcessResult
    from codex_flow.merge import MergeRunner

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "switch", "-c", "codex/demo"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "codex-flow@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Codex Flow"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# Test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)
    plan_dir = tmp_path / ".codex-flow" / "plans" / "demo"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.md").write_text("Branch: codex/demo\nTitle: Demo\n\n### Commit 1: Done\n\nDone\n", encoding="utf-8")
    (plan_dir / "log.md").write_text("- Completed commit unit 1.\n", encoding="utf-8")
    _, queue = plans.load_queue(plan_dir / "plan.md")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    ledger_path = plan_dir / "attempts" / queue["units"][0]["id"] / "main" / "attempt-ledger.json"
    AttemptLedger(ledger_path).compare_and_set(
        0,
        {
            "owner": "main", "status": "completed", "unit_id": queue["units"][0]["id"],
            "attempt": 1, "commit": commit, "evidence_sha256": "a" * 64,
        },
    )
    queue["units"][0].update(
        {
            "commit": commit,
            "verification_evidence_sha256": "a" * 64,
            "main_unit_ledger": str(ledger_path.relative_to(plan_dir)),
            "main_unit_ledger_revision": 1,
        }
    )
    plans.save_queue(plan_dir, queue)
    identity = final_evidence_identity(plan_dir / "plan.md")
    produce_final_gate(
        plan_dir / "plan.md",
        review_evidence={**identity, "status": "pass"},
        test_evidence={**identity, "status": "pass"},
    )
    pr.write_pr_lock(tmp_path, "codex/demo", "https://github.com/example/repo/pull/7", "reviewing")

    monkeypatch.setattr(merge, "push_branch", lambda repo, branch: None)
    monkeypatch.setattr(merge, "fetch_branch", lambda repo, branch: ProcessResult(["git", "fetch", "origin", branch], 0, "", ""))
    monkeypatch.setattr(merge, "merge_branch", lambda repo, source, target: ProcessResult(["git", "merge", source], 0, "", ""))
    monkeypatch.setattr(MergeRunner, "finalize_source_branch", lambda self, *args, **kwargs: (True, "branch_closed: codex/demo"))

    def fake_run_process(args, cwd, input_text=None):
        if args[:3] == ["gh", "pr", "list"]:
            return ProcessResult(args=args, status=0, stdout='[{"url":"https://github.com/example/repo/pull/7","title":"Demo","isDraft":false}]', stderr="")
        if args[:3] == ["gh", "pr", "merge"]:
            return ProcessResult(args=args, status=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(merge, "run_process", fake_run_process)

    result = MergeRunner().merge_remote(plan_dir / "plan.md", execute=True)

    assert result.action == "merged_remote"
    assert "pr_lock_cleared=true" in result.message
    assert not pr.read_pr_lock(tmp_path)


def test_run_all_open_pr_writes_dry_run_after_units_done(tmp_path, capsys):
    ticket = tickets.submit_ticket("Open PR dry run", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)
    (plan.directory / "log.md").write_text(
        "# Log\n\n- Completed commit unit 1.\n- Completed commit unit 2.\n- Completed commit unit 3.\n",
        encoding="utf-8",
    )

    status = cli.main(["--repo", str(tmp_path), "run-all", "--plan", str(plan.plan_path), "--open-pr"])

    output = capsys.readouterr().out
    assert status == 0
    assert "pr_dry_run:" in output
    assert (plan.directory / "pr-dry-run.md").exists()


def test_remote_pr_creation_refuses_active_pr_lock(tmp_path, capsys):
    ticket = tickets.submit_ticket("Remote PR lock test", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)
    (plan.directory / "log.md").write_text(
        "# Log\n\n- Completed commit unit 1.\n- Completed commit unit 2.\n- Completed commit unit 3.\n",
        encoding="utf-8",
    )
    pr.write_pr_lock(tmp_path, "codex/other", "https://github.com/example/repo/pull/8", "reviewing")

    status = cli.main(["--repo", str(tmp_path), "create-pr", "--plan", str(plan.plan_path)])

    output = capsys.readouterr().out
    assert status == 1
    assert "pr_locked: active" in output
    assert "codex/other" in output


def test_run_all_remote_returns_nonzero_when_pr_lock_active(tmp_path, capsys):
    ticket = tickets.submit_ticket("Run all remote lock test", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)
    (plan.directory / "log.md").write_text(
        "# Log\n\n- Completed commit unit 1.\n- Completed commit unit 2.\n- Completed commit unit 3.\n",
        encoding="utf-8",
    )
    pr.write_pr_lock(tmp_path, "codex/other", "https://github.com/example/repo/pull/8", "reviewing")

    status = cli.main(["--repo", str(tmp_path), "run-all", "--plan", str(plan.plan_path), "--remote"])

    output = capsys.readouterr().out
    assert status == 1
    assert "pr_locked: active" in output


def test_run_all_failure_state_returns_nonzero(tmp_path, capsys):
    ticket = tickets.submit_ticket("Run all exit code test", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)

    status = cli.main(["--repo", str(tmp_path), "run-all", "--plan", str(plan.plan_path), "--max-units", "0"])

    output = capsys.readouterr().out
    assert status == 1
    assert "max_units_reached" in output


def test_create_pr_incomplete_plan_returns_nonzero(tmp_path, capsys):
    ticket = tickets.submit_ticket("Incomplete remote PR test", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)

    status = cli.main(["--repo", str(tmp_path), "create-pr", "--plan", str(plan.plan_path)])

    output = capsys.readouterr().out
    assert status == 1
    assert "Plan is not complete" in output
