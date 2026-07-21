from __future__ import annotations

import subprocess

from codex_flow import cli, inbox, plans
from codex_flow.attempt_ledger import AttemptLedger
from codex_flow.git_ops import ProcessResult
from codex_flow.merge import MergeResult, MergeRunner
from codex_flow.final_gate import final_evidence_identity, produce_final_gate


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
    plan_dir = tmp_path / ".codex-flow" / "plans" / "demo"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.md").write_text("Branch: codex/demo\nTitle: Demo\n\n### Commit 1: Feature\n\nDone\n", encoding="utf-8")
    (plan_dir / "log.md").write_text("- Completed commit unit 1.\n", encoding="utf-8")
    _, queue = plans.load_queue(plan_dir / "plan.md")
    _attach_main_provenance(plan_dir, queue, tmp_path)
    plans.save_queue(plan_dir, queue)
    identity = final_evidence_identity(plan_dir / "plan.md")
    produce_final_gate(
        plan_dir / "plan.md",
        review_evidence={**identity, "status": "pass"},
        test_evidence={**identity, "status": "pass"},
    )

    result = MergeRunner().merge_local(plan_dir / "plan.md", target="main", execute=True)

    assert result.action == "merged_local"
    assert (tmp_path / "feature.txt").read_text(encoding="utf-8") == "feature\n"
    assert subprocess.run(["git", "branch", "--list", "codex/demo"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip() == ""


def test_merge_runner_reports_branch_finalization_blocked_when_safe_delete_fails(tmp_path, monkeypatch):
    init_git_repo(tmp_path)
    subprocess.run(["git", "switch", "-c", "codex/demo"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "feature.txt").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=tmp_path, check=True, capture_output=True)
    plan_dir = tmp_path / ".codex-flow" / "plans" / "demo"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.md").write_text("Branch: codex/demo\nTitle: Demo\n\n### Commit 1: Feature\n\nDone\n", encoding="utf-8")
    (plan_dir / "log.md").write_text("- Completed commit unit 1.\n", encoding="utf-8")
    _, queue = plans.load_queue(plan_dir / "plan.md")
    _attach_main_provenance(plan_dir, queue, tmp_path)
    plans.save_queue(plan_dir, queue)
    identity = final_evidence_identity(plan_dir / "plan.md")
    produce_final_gate(
        plan_dir / "plan.md",
        review_evidence={**identity, "status": "pass"},
        test_evidence={**identity, "status": "pass"},
    )
    monkeypatch.setattr(
        "codex_flow.merge.delete_local_branch",
        lambda _repo, branch: ProcessResult(["git", "branch", "-d", branch], 1, "", "branch is checked out"),
    )

    result = MergeRunner().merge_local(plan_dir / "plan.md", target="main", execute=True)

    assert result.action == "branch_finalization_blocked"
    assert "branch_close_held" in result.message


def test_branch_finalization_blocked_returns_failure_exit_code():
    assert cli.exit_code_for_action("branch_finalization_blocked") == 1


def test_merge_cli_returns_failure_when_branch_finalization_is_blocked(tmp_path, monkeypatch, capsys):
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("Title: Demo\n", encoding="utf-8")

    class StubMergeRunner:
        def merge_local(self, *_args, **_kwargs):
            return MergeResult("branch_finalization_blocked", "branch_finalization_blocked: held")

    monkeypatch.setattr(cli, "MergeRunner", StubMergeRunner)

    status = cli.main(["merge", "--plan", str(plan_path), "--execute"])

    assert status == 1
    assert "branch_finalization_blocked: held" in capsys.readouterr().out


def _attach_main_provenance(plan_dir, queue, repo):
    unit = queue["units"][0]
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    ledger_path = plan_dir / "attempts" / unit["id"] / "main" / "attempt-ledger.json"
    AttemptLedger(ledger_path).compare_and_set(
        0,
        {
            "owner": "main", "status": "completed", "unit_id": unit["id"],
            "attempt": 1, "commit": commit, "evidence_sha256": "a" * 64,
        },
    )
    unit.update(
        {
            "commit": commit,
            "verification_evidence_sha256": "a" * 64,
            "main_unit_ledger": str(ledger_path.relative_to(plan_dir)),
            "main_unit_ledger_revision": 1,
        }
    )


def init_git_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "switch", "-c", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "codex-flow@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Codex Flow"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# Test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)
