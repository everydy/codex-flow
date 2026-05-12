from __future__ import annotations

import subprocess

from codex_flow import inbox
from codex_flow.merge import MergeRunner


def test_drain_inbox_requests_routes_all_until_lock(tmp_path):
    inbox_path = tmp_path / ".codex-flow" / "inbox.md"
    inbox.write_inbox_requests(
        inbox_path,
        [
            inbox.QueuedRequest("First", "lock", "2026-05-12T00:00:00"),
            inbox.QueuedRequest("Second", "lock", "2026-05-12T00:01:00"),
        ],
    )
    routed: list[str] = []

    result = inbox.drain_inbox_requests(inbox_path, lambda: False, lambda request: routed.append(request.prompt) or "ok")

    assert result.action == "drained"
    assert routed == ["First", "Second"]
    assert inbox.read_inbox_requests(inbox_path) == []


def test_merge_runner_local_merge_success(tmp_path):
    init_git_repo(tmp_path)
    subprocess.run(["git", "switch", "-c", "codex/demo"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "feature.txt").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "switch", "main"], cwd=tmp_path, check=True, capture_output=True)
    plan_dir = tmp_path / ".codex-flow" / "plans" / "demo"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.md").write_text("Branch: codex/demo\nTitle: Demo\n\n### Commit 1: Feature\n\nDone\n", encoding="utf-8")
    (plan_dir / "log.md").write_text("- Completed commit unit 1.\n", encoding="utf-8")

    result = MergeRunner().merge_local(plan_dir / "plan.md", target="main", execute=True)

    assert result.action == "merged_local"
    assert (tmp_path / "feature.txt").read_text(encoding="utf-8") == "feature\n"


def init_git_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "switch", "-c", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "codex-flow@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Codex Flow"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# Test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)
