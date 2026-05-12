from __future__ import annotations

import json
import subprocess

from codex_flow import cli, plans, pr, tickets
from codex_flow.dashboard import render_dashboard


def test_route_reuses_single_active_plan(tmp_path, capsys):
    first = tickets.submit_ticket("Improve dashboard", repo=tmp_path)
    plan = plans.create_plan_from_ticket(first.path, repo=tmp_path)

    status = cli.main(["--repo", str(tmp_path), "route", "Improve dashboard copy"])

    output = capsys.readouterr().out
    assert status == 0
    assert "route_to_existing_plan:" in output
    assert "Improve dashboard copy" in (plan.directory / "requests.md").read_text(encoding="utf-8")


def test_route_explicit_plan_appends_request(tmp_path, capsys):
    first = tickets.submit_ticket("Plan target", repo=tmp_path)
    plan = plans.create_plan_from_ticket(first.path, repo=tmp_path)

    status = cli.main(["--repo", str(tmp_path), "route", "Attach this", "--plan", str(plan.plan_path), "--reason", "Manual attach"])

    output = capsys.readouterr().out
    assert status == 0
    assert "route_to_existing_plan:" in output
    requests = (plan.directory / "requests.md").read_text(encoding="utf-8")
    assert "Attach this" in requests
    assert "Manual attach" in requests


def test_dashboard_renders_plan_progress_and_suggested_command(tmp_path):
    ticket = tickets.submit_ticket("Dashboard plan", repo=tmp_path)
    plans.create_plan_from_ticket(ticket.path, repo=tmp_path)

    output = render_dashboard(tmp_path)

    assert "PR lock: none" in output
    assert "Active Plans" in output
    assert "Suggested command:" in output
    assert "run-all" in output


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
    assert "drain: planned" in output
    assert not pr.read_pr_lock(tmp_path)
    assert tickets.load_ticket(ticket.path).status == "planned"


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
