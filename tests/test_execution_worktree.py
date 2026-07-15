from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from codex_flow import cli, git_ops, plans, runner
from codex_flow.codex_cli import CHILD_MANIFEST_ENV
from codex_flow.run_all import RunAllRunner


@pytest.fixture(autouse=True)
def isolate_attestation_preflight(tmp_path, monkeypatch):
    counter = iter(range(1000))

    def attestation(**_kwargs):
        nonce = f"worktree-test-nonce-{next(counter)}"
        return SimpleNamespace(nonce=nonce, to_dict=lambda: {"nonce": nonce})

    monkeypatch.setenv(CHILD_MANIFEST_ENV, str(tmp_path / "test-child-manifest.json"))
    monkeypatch.setattr(runner, "generate_child_attestation", attestation)
    monkeypatch.setattr(runner, "verify_child_attestation", lambda value, **_kwargs: value)


def init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "switch", "-c", "main"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "codex-flow@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Codex Flow"], cwd=repo, check=True)
    (repo / "README.md").write_text("# Test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True, text=True)


def write_source_plan(repo: Path) -> Path:
    source = repo / "docs" / "plans" / "example-plan.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            [
                "# Example Plan",
                "",
                "## Implementation Plan",
                "",
                "### Commit 1: First unit",
                "",
                "- target files:",
                "  - `work.txt`",
                "",
                "### Commit 2: Second unit",
                "",
                "- target files:",
                "  - `work.txt`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return source


def write_fake_codex(root: Path) -> Path:
    command = root / "fake-codex.py"
    command.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
output = pathlib.Path(args[args.index("--output-last-message") + 1])
if "resume" in args:
    output.write_text(
        'REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="clean"\\n'
        'COMMIT_UNIT_READY title="Worktree unit" summary="updated work.txt"\\n',
        encoding="utf-8",
    )
else:
    pathlib.Path("work.txt").write_text("execution repo only\\n", encoding="utf-8")
    output.write_text("implementation complete\\n", encoding="utf-8")
print('{"session_id":"fake-session"}')
""",
        encoding="utf-8",
    )
    command.chmod(command.stat().st_mode | 0o111)
    return command


def write_two_unit_fake_codex(root: Path) -> Path:
    command = root / "fake-two-unit-codex.py"
    command.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
output = pathlib.Path(args[args.index("--output-last-message") + 1])
if "resume" in args:
    output.write_text(
        'REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="clean"\\n'
        'COMMIT_UNIT_READY title="Disposable unit" summary="reviewed"\\n',
        encoding="utf-8",
    )
else:
    counter = pathlib.Path(__file__).with_suffix(".count")
    number = int(counter.read_text(encoding="utf-8")) + 1 if counter.exists() else 1
    counter.write_text(str(number), encoding="utf-8")
    pathlib.Path(f"unit-{number}.txt").write_text(f"unit {number}\\n", encoding="utf-8")
    output.write_text("implementation complete\\n", encoding="utf-8")
print('{"session_id":"fake-session"}')
""",
        encoding="utf-8",
    )
    command.chmod(command.stat().st_mode | 0o111)
    return command


def test_route_persists_external_execution_worktree_without_switching_dirty_source(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    source = write_source_plan(repo)
    dirty = repo / "operator-notes.txt"
    dirty.write_text("keep me\n", encoding="utf-8")
    worktree_root = tmp_path / "worktrees"

    status = cli.main(
        [
            "--repo",
            str(repo),
            "route",
            str(source),
            "--worktree-root",
            str(worktree_root),
        ]
    )

    assert status == 0
    assert git_ops.current_branch(repo) == "main"
    assert dirty.read_text(encoding="utf-8") == "keep me\n"
    plan_dir = next((repo / ".codex-flow" / "plans").glob("*"))
    queue = json.loads((plan_dir / "queue.json").read_text(encoding="utf-8"))
    execution_repo = worktree_root / queue["plan_slug"]
    assert Path(queue["source_repo"]) == repo.resolve()
    assert Path(queue["execution_repo"]) == execution_repo.resolve()
    assert Path(queue["worktree_path"]) == execution_repo.resolve()
    assert queue["branch"] == f"codex/{queue['plan_slug']}"
    assert queue["source_plan_sha256"] == queue["source_plan"]["sha256"]
    assert queue["cleanup_state"] == "active"
    assert git_ops.current_branch(execution_repo) == queue["branch"]
    assert plans.execution_repo_for_plan(plan_dir) == execution_repo.resolve()


def test_cleanup_execution_worktree_refuses_dirty_and_unmerged_branches(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    dirty_context = git_ops.prepare_execution_worktree(repo, "dirty", "codex/dirty", tmp_path / "worktrees")
    (dirty_context.execution_repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="dirty"):
        git_ops.cleanup_execution_worktree(dirty_context, target_branch="main")

    (dirty_context.execution_repo / "dirty.txt").unlink()
    unmerged_context = git_ops.prepare_execution_worktree(repo, "unmerged", "codex/unmerged", tmp_path / "worktrees")
    (unmerged_context.execution_repo / "unit.txt").write_text("unit\n", encoding="utf-8")
    subprocess.run(["git", "add", "unit.txt"], cwd=unmerged_context.execution_repo, check=True)
    subprocess.run(["git", "commit", "-m", "unit"], cwd=unmerged_context.execution_repo, check=True, capture_output=True)

    with pytest.raises(SystemExit, match="not merged"):
        git_ops.cleanup_execution_worktree(unmerged_context, target_branch="main")


def test_run_next_edits_and_commits_only_in_execution_worktree(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CODEX_FLOW_CHILD_ISOLATION", "0")
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    source = write_source_plan(repo)
    worktree_root = tmp_path / "worktrees"
    fake_codex = write_fake_codex(tmp_path)
    assert cli.main(["--repo", str(repo), "route", str(source), "--worktree-root", str(worktree_root)]) == 0
    capsys.readouterr()
    plan_dir = next((repo / ".codex-flow" / "plans").glob("*"))
    queue = json.loads((plan_dir / "queue.json").read_text(encoding="utf-8"))
    queue["units"][0]["allowed_paths"].append("work.txt")
    (plan_dir / "queue.json").write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    execution_repo = Path(queue["execution_repo"])
    refreshed_repos: list[Path] = []
    monkeypatch.setattr(runner.state, "refresh_dashboard", lambda path: refreshed_repos.append(Path(path).resolve()))

    status = cli.main(
        [
            "run-next",
            "--plan",
            str(plan_dir / "plan.md"),
            "--allow-dirty",
            "--codex-command",
            str(fake_codex),
        ]
    )

    assert status == 0
    assert not (repo / "work.txt").exists()
    assert (execution_repo / "work.txt").read_text(encoding="utf-8") == "execution repo only\n"
    source_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    execution_parent = subprocess.run(["git", "rev-parse", "HEAD^"], cwd=execution_repo, check=True, capture_output=True, text=True).stdout.strip()
    assert source_head == execution_parent
    assert refreshed_repos[-1] == repo.resolve()


def test_cleanup_worktree_cli_removes_clean_merged_worktree_and_updates_metadata(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    source = write_source_plan(repo)
    worktree_root = tmp_path / "worktrees"
    assert cli.main(["--repo", str(repo), "route", str(source), "--worktree-root", str(worktree_root)]) == 0
    capsys.readouterr()
    plan_dir = next((repo / ".codex-flow" / "plans").glob("*"))
    queue = json.loads((plan_dir / "queue.json").read_text(encoding="utf-8"))
    execution_repo = Path(queue["execution_repo"])

    status = cli.main(["cleanup-worktree", "--plan", str(plan_dir / "plan.md"), "--target", "main"])

    output = capsys.readouterr().out
    persisted = json.loads((plan_dir / "queue.json").read_text(encoding="utf-8"))
    assert status == 0
    assert "worktree_cleaned:" in output
    assert not execution_repo.exists()
    assert persisted["cleanup_state"] == "removed"
    remaining_branch = subprocess.run(
        ["git", "branch", "--list", queue["branch"]],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert remaining_branch == ""


def test_disposable_two_unit_graph_resumes_without_merge_then_cleans_up_safely(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_FLOW_CHILD_ISOLATION", "0")
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    source_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    source = write_source_plan(repo)
    fake_codex = write_two_unit_fake_codex(tmp_path)
    worktree_root = tmp_path / "worktrees"
    assert cli.main(["--repo", str(repo), "route", str(source), "--worktree-root", str(worktree_root)]) == 0
    plan_dir = next((repo / ".codex-flow" / "plans").glob("*"))
    queue = json.loads((plan_dir / "queue.json").read_text(encoding="utf-8"))
    execution_repo = Path(queue["execution_repo"])
    queue["units"][0]["allowed_paths"] = ["unit-1.txt"]
    queue["units"][1]["allowed_paths"] = ["unit-2.txt"]
    (plan_dir / "queue.json").write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    first = runner.run_next(
        plan_dir / "plan.md", execute=True, commit=True, codex_command=str(fake_codex)
    )
    resumed = RunAllRunner().run_all(
        plan_dir / "plan.md",
        execute=True,
        commit=True,
        codex_command=str(fake_codex),
        no_merge=True,
    )

    persisted = json.loads((plan_dir / "queue.json").read_text(encoding="utf-8"))
    graph = subprocess.run(
        ["git", "log", "--format=%s", "--reverse"],
        cwd=execution_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    first_paths = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD~1"],
        cwd=execution_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    second_paths = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=execution_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    review_0 = json.loads(
        (plan_dir / "attempts" / "unit-001" / "attempt-0-review.json").read_text(encoding="utf-8")
    )
    review_1 = json.loads(
        (plan_dir / "attempts" / "unit-002" / "attempt-0-review.json").read_text(encoding="utf-8")
    )

    assert first["action"] == "committed"
    assert resumed.action == "local_branch"
    assert len(resumed.steps) == 1
    assert [unit["status"] for unit in persisted["units"]] == ["done", "done"]
    assert len(graph) == 3
    assert first_paths == ["unit-1.txt"]
    assert second_paths == ["unit-2.txt"]
    assert review_0["child_attestation"] != review_1["child_attestation"]
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip() == source_head
    assert execution_repo.exists()
    assert persisted["cleanup_state"] == "active"

    subprocess.run(["git", "merge", "--ff-only", queue["branch"]], cwd=repo, check=True, capture_output=True)
    assert cli.main(["cleanup-worktree", "--plan", str(plan_dir / "plan.md"), "--target", "main"]) == 0
    cleaned = json.loads((plan_dir / "queue.json").read_text(encoding="utf-8"))
    assert cleaned["cleanup_state"] == "removed"
    assert not execution_repo.exists()
