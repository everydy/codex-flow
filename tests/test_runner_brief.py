from __future__ import annotations

import json

import subprocess

from codex_flow import briefs, cli, plans, pr, runner, tickets


def make_plan(tmp_path):
    ticket = tickets.submit_ticket("아침 리뷰 테스트", repo=tmp_path)
    return plans.create_plan_from_ticket(ticket.path, repo=tmp_path)


def init_git_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "switch", "-c", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "codex-flow@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Codex Flow"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# Test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)


def write_fake_codex(tmp_path):
    fake_codex = tmp_path.parent / f"fake_codex_{tmp_path.name}.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
output = pathlib.Path(args[args.index("--output-last-message") + 1])
work = pathlib.Path("work.txt")
previous = work.read_text(encoding="utf-8") if work.exists() else ""
work.write_text(previous + "implemented\\n", encoding="utf-8")
output.write_text('COMMIT_UNIT_READY title="Fake implementation" summary="changed work.txt"\\n', encoding="utf-8")
print('{"session_id":"fake-session"}')
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | 0o111)
    return fake_codex


def test_run_next_writes_prompt_and_marks_unit_prompted(tmp_path):
    plan = make_plan(tmp_path)

    result = runner.run_next(plan.plan_path)

    assert result is not None
    assert result["prompt_path"].exists()
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert queue["units"][0]["status"] == "prompted"
    assert queue["units"][0]["prompt_path"] == "prompts/unit-001.md"


def test_run_all_respects_max_units(tmp_path):
    plan = make_plan(tmp_path)

    results = runner.run_all(plan.plan_path, max_units=2)

    assert len(results) == 2
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert [unit["status"] for unit in queue["units"]] == ["prompted", "prompted", "ready"]


def test_morning_brief_review_and_pr_dry_run(tmp_path):
    plan = make_plan(tmp_path)
    runner.run_next(plan.plan_path)

    brief = briefs.write_morning_brief(tmp_path)
    review = briefs.write_review(plan.plan_path)
    pr = briefs.write_pr_dry_run(plan.plan_path)

    assert brief.exists()
    assert review.exists()
    assert pr.exists()
    assert "remote PR" in pr.read_text(encoding="utf-8") or "Remote PR" in pr.read_text(encoding="utf-8")


def test_run_next_execute_with_fake_codex_commits_unit(tmp_path):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex(tmp_path)

    plan = make_plan(tmp_path)
    result = runner.run_next(plan.plan_path, execute=True, commit=True, codex_command=str(fake_codex))

    assert result["action"] == "committed"
    assert result["commit"]
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert queue["units"][0]["status"] == "done"
    assert queue["units"][0]["changed_paths"] == ["work.txt"]
    assert subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.count("\n") == 2


def test_run_next_auto_resolve_shelves_dirty_worktree_before_execution(tmp_path):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex(tmp_path)
    (tmp_path / "dirty-note.md").write_text("preserve me\n", encoding="utf-8")

    plan = make_plan(tmp_path)
    result = runner.run_next(plan.plan_path, execute=True, commit=True, codex_command=str(fake_codex), auto_resolve=True)

    assert result["action"] == "committed"
    assert result["auto_resolved_dirty"] == ["dirty-note.md"]
    stash_list = subprocess.run(["git", "stash", "list"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    assert "codex-flow auto-shelve" in stash_list


def test_route_queues_when_pr_lock_is_active(tmp_path, capsys):
    pr.write_pr_lock(tmp_path, "codex/open", "https://github.com/example/repo/pull/1", "reviewing")

    status = cli.main(["--repo", str(tmp_path), "route", "새 작업", "--auto-resolve"])

    output = capsys.readouterr().out
    assert status == 0
    assert "queued due to active PR lock" in output
    assert not list((tmp_path / ".codex-flow" / "plans").glob("*"))


def test_pr_lock_check_drain_and_merge_hard_stop(tmp_path):
    ticket = tickets.submit_ticket("PR 테스트", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)

    assert pr.check_pr_lock(tmp_path) == "pr_lock: none"
    dry = pr.write_pr_dry_run(plan.plan_path)
    assert dry.exists()
    assert pr.merge_plan(plan.plan_path).startswith("merge: needs_work")

    pr.write_pr_lock(tmp_path, "codex/test", "https://github.com/example/repo/pull/1", "reviewing")
    assert "active" in pr.check_pr_lock(tmp_path)
    assert pr.drain_inbox(tmp_path) == "drain: locked"


def test_open_pr_auto_resolve_executes_unfinished_units_before_dry_run(tmp_path, capsys):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex(tmp_path)
    plan = make_plan(tmp_path)

    status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "open-pr",
            "--plan",
            str(plan.plan_path),
            "--auto-resolve",
            "--execute-units",
            "--commit",
            "--codex-command",
            str(fake_codex),
        ]
    )

    output = capsys.readouterr().out
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert status == 0
    assert "auto_resolve_units:" in output
    assert all(unit["status"] == "done" for unit in queue["units"])
    assert (plan.directory / "pr-dry-run.md").exists()


def test_merge_auto_resolve_executes_unfinished_units_and_merges_without_execute_flag(tmp_path, capsys):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex(tmp_path)
    plan = make_plan(tmp_path)

    status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "merge",
            "--plan",
            str(plan.plan_path),
            "--target",
            "main",
            "--auto-resolve",
            "--execute-units",
            "--commit",
            "--codex-command",
            str(fake_codex),
        ]
    )

    output = capsys.readouterr().out
    assert status == 0
    assert "auto_resolve_units:" in output
    assert "merge: local merged" in output
    assert subprocess.run(["git", "branch", "--show-current"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip() == "main"
