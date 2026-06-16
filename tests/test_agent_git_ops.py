from __future__ import annotations

import subprocess

from codex_flow.agent import parse_agent_decision
from codex_flow.git_ops import (
    changed_paths_since,
    current_branch,
    dirty_paths,
    ensure_worktree,
    parse_status,
    scoped_status_summary,
    stash_paths,
    status,
)


def init_git_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "switch", "-c", "main"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp_path, check=True, capture_output=True, text=True)


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


def test_parse_status_z_handles_non_ascii_paths():
    snapshot = parse_status("?? 디자인올인원/SKILL.md\0 M src/app.py\0")

    assert [entry.path for entry in snapshot.entries] == ["디자인올인원/SKILL.md", "src/app.py"]
    assert snapshot.raw == "?? 디자인올인원/SKILL.md\n M src/app.py"


def test_status_and_stash_paths_handle_non_ascii_untracked_paths(tmp_path):
    init_git_repo(tmp_path)

    target = tmp_path / "디자인올인원" / "SKILL.md"
    target.parent.mkdir()
    target.write_text("dirty\n", encoding="utf-8")

    paths = dirty_paths(status(tmp_path))
    assert paths == ["디자인올인원/SKILL.md"]

    stash_paths(tmp_path, paths, "test non-ascii stash")

    assert not target.exists()
    stash_list = subprocess.run(["git", "stash", "list"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    assert "test non-ascii stash" in stash_list


def test_ensure_worktree_creates_and_reuses_task_branch(tmp_path):
    init_git_repo(tmp_path)
    worktree_path = tmp_path.parent / f"{tmp_path.name}-worktree"

    created = ensure_worktree(tmp_path, "codex/demo", worktree_path)
    reused = ensure_worktree(tmp_path, "codex/demo", worktree_path)

    assert created == worktree_path.resolve()
    assert reused == created
    assert current_branch(created) == "codex/demo"
    assert current_branch(tmp_path) == "main"


def test_ensure_worktree_rejects_existing_path_on_wrong_branch(tmp_path):
    init_git_repo(tmp_path)
    worktree_path = tmp_path.parent / f"{tmp_path.name}-worktree"
    ensure_worktree(tmp_path, "codex/other", worktree_path)

    try:
        ensure_worktree(tmp_path, "codex/demo", worktree_path)
    except SystemExit as exc:
        assert "expected codex/demo" in str(exc)
    else:
        raise AssertionError("ensure_worktree should reject a mismatched existing branch")


def test_scoped_status_summary_hides_unrelated_paths():
    snapshot = parse_status(" M codex_flow/runner.py\n M unrelated.txt\n?? tests/test_runner_brief.py\n")

    summary = scoped_status_summary(snapshot, ["codex_flow/**", "tests/**"])

    assert "codex_flow/runner.py" in summary
    assert "tests/test_runner_brief.py" in summary
    assert "unrelated.txt" not in summary
    assert "1 unrelated dirty path(s) hidden" in summary
