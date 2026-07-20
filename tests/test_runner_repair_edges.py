from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from codex_flow import cli, runner
from codex_flow.codex_cli import CHILD_MANIFEST_ENV
from tests.test_runner_brief import init_git_repo, make_plan, write_fake_codex_dirty_needs_work


@pytest.fixture(autouse=True)
def isolate_attestation_preflight(tmp_path, monkeypatch):
    counter = iter(range(1000))

    def attestation(**_kwargs):
        nonce = f"repair-edge-nonce-{next(counter)}"
        return SimpleNamespace(nonce=nonce, to_dict=lambda: {"nonce": nonce})

    monkeypatch.setenv(CHILD_MANIFEST_ENV, str(tmp_path.parent / "test-child-manifest.json"))
    monkeypatch.setenv("CODEX_FLOW_CHILD_ISOLATION", "0")
    monkeypatch.setattr(runner, "generate_child_attestation", attestation)
    monkeypatch.setattr(runner, "verify_child_attestation", lambda value, **_kwargs: value)
    monkeypatch.setattr(
        runner,
        "ensure_prepared_child_runtime",
        lambda **_kwargs: SimpleNamespace(
            home=tmp_path.parent / "test-child-home",
            manifest=tmp_path.parent / "test-child-manifest.json",
            source="test",
            cache_key="test-key",
            reused=True,
        ),
    )


def write_fake_codex_delete_partial_ready(tmp_path: Path) -> Path:
    fake_codex = tmp_path.parent / f"fake_delete_partial_{tmp_path.name}.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()
output = pathlib.Path(args[args.index("--output-last-message") + 1])
work = pathlib.Path("work.txt")
if "Agent 3: Read-only Reviewer" not in prompt:
    if work.exists():
        work.unlink()
    output.write_text("implementation phase\\n", encoding="utf-8")
    print('{"session_id":"implementation-session"}')
else:
    output.write_text(
        'INTERNAL_REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="clean"\\n'
        'COMMIT_UNIT_READY title="Deleted stale partial" summary="removed stale partial"\\n',
        encoding="utf-8",
    )
    print('{"session_id":"review-session"}')
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | 0o111)
    return fake_codex


def run_next_auto_resolve(repo: Path, plan_path: Path, codex_command: Path) -> int:
    return cli.main(
        [
            "--repo",
            str(repo),
            "run-next",
            "--plan",
            str(plan_path),
            "--auto-resolve",
            "--codex-command",
            str(codex_command),
        ]
    )


def test_resume_repair_can_delete_untracked_partial_without_committing_missing_path(tmp_path, capsys):
    init_git_repo(tmp_path)
    plan = make_plan(tmp_path)
    failing_codex = write_fake_codex_dirty_needs_work(tmp_path)
    deleting_codex = write_fake_codex_delete_partial_ready(tmp_path)

    first_status = run_next_auto_resolve(tmp_path, plan.plan_path, failing_codex)
    capsys.readouterr()
    second_status = run_next_auto_resolve(tmp_path, plan.plan_path, deleting_codex)

    output = capsys.readouterr().out
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert first_status == 1
    assert second_status == 0
    assert "action: skipped" in output
    assert queue["units"][0]["status"] == "done"
    assert queue["units"][0]["changed_paths"] == []
    assert not (tmp_path / "work.txt").exists()


def test_resume_repair_preserves_tracked_deletion_as_committable_change(tmp_path, capsys):
    init_git_repo(tmp_path)
    (tmp_path / "work.txt").write_text("tracked base\\n", encoding="utf-8")
    subprocess.run(["git", "add", "work.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "add tracked work"], cwd=tmp_path, check=True, capture_output=True)
    plan = make_plan(tmp_path)
    failing_codex = write_fake_codex_dirty_needs_work(tmp_path)
    deleting_codex = write_fake_codex_delete_partial_ready(tmp_path)

    first_status = run_next_auto_resolve(tmp_path, plan.plan_path, failing_codex)
    capsys.readouterr()
    second_status = run_next_auto_resolve(tmp_path, plan.plan_path, deleting_codex)

    output = capsys.readouterr().out
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    last_commit_names = subprocess.run(
        ["git", "show", "--name-status", "--format="],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert first_status == 1
    assert second_status == 0
    assert "action: committed" in output
    assert queue["units"][0]["status"] == "done"
    assert queue["units"][0]["changed_paths"] == ["work.txt"]
    assert "D\twork.txt" in last_commit_names
    assert not (tmp_path / "work.txt").exists()
