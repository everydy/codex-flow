from __future__ import annotations

import json

import subprocess
from types import SimpleNamespace

import pytest

from codex_flow import briefs, cli, plans, pr, runner, source_plan, tickets
from codex_flow.codex_cli import CHILD_MANIFEST_ENV
from codex_flow.child_runtime import REQUIRE_EXPLICIT_CHILD_ENV


def test_attempt_derived_views_regenerate_from_ledger_revision(tmp_path):
    review = tmp_path / "review.json"
    handoff = tmp_path / "handoff.json"
    review.write_text('{"ledger_revision": 1}\n', encoding="utf-8")
    handoff.write_text('{"ledger_revision": 2}\n', encoding="utf-8")
    unit = {"attempt_ledger_revision": 3}

    runner.ensure_attempt_view_revision(
        9,
        queue_unit=unit,
        review_path=review,
        handoff_path=handoff,
    )

    assert unit["attempt_ledger_revision"] == 9
    assert json.loads(review.read_text())["ledger_revision"] == 9
    assert json.loads(handoff.read_text())["ledger_revision"] == 9


@pytest.fixture(autouse=True)
def isolate_attestation_preflight(tmp_path, monkeypatch):
    counter = iter(range(1000))

    def attestation(**_kwargs):
        nonce = f"test-nonce-{next(counter)}"
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


def make_plan(tmp_path, *, isolated=True):
    ticket = tickets.submit_ticket("아침 리뷰 테스트", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    for unit in queue["units"]:
        unit["allowed_paths"] = list(dict.fromkeys([*unit.get("allowed_paths", []), "work.txt"]))
        if isolated:
            unit.setdefault("execution_policy", {})["executor_adapter"] = "isolated-child"
            unit["execution_policy"]["review_policy"] = "per_unit"
    plan.queue_json.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan


def test_queue_sync_persists_effective_execution_policy(tmp_path):
    plan = make_plan(tmp_path)

    queue = plans.load_queue(plan.plan_path)[1]
    policy = queue["units"][0]["execution_policy"]

    assert policy["policy_version"]
    assert policy["effective_profile"] == "contract"
    assert policy["execution_mode"] == "isolated_child"
    assert policy["executor_adapter"] == "isolated-child"
    assert policy["unit_gate"] == "contract"
    assert policy["review_policy"] == "per_unit"
    assert policy["inference_reasons"]


def test_run_next_default_main_path_does_not_construct_child(tmp_path, monkeypatch):
    init_git_repo(tmp_path)
    plan = make_plan(tmp_path, isolated=False)

    monkeypatch.setattr(
        runner,
        "CodexImplementerAgent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("child must not be constructed")),
    )
    result = runner.run_next(plan.plan_path, execute=True, commit=True)

    assert result["action"] == "main_handoff"
    assert result["contract"]["owner"] == "main"
    assert result["contract"]["ledger_revision"] == 1


def test_run_next_execute_skips_queue_unit_already_marked_done(tmp_path, monkeypatch):
    init_git_repo(tmp_path)
    plan = make_plan(tmp_path, isolated=False)
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    queue["units"][0]["status"] = "done"
    plan.queue_json.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "CodexImplementerAgent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("child must not be constructed")),
    )

    result = runner.run_next(plan.plan_path, execute=True, commit=True)

    assert result["unit"]["id"] == "unit-002"
    assert result["action"] == "main_handoff"


def test_portable_isolated_preflight_denies_before_plan_or_repo_write(tmp_path, monkeypatch):
    init_git_repo(tmp_path)
    plan = make_plan(tmp_path, isolated=True)
    monkeypatch.setenv(REQUIRE_EXPLICIT_CHILD_ENV, "1")
    monkeypatch.delenv("CODEX_FLOW_CHILD_HOME", raising=False)
    monkeypatch.delenv(CHILD_MANIFEST_ENV, raising=False)
    monkeypatch.setattr(
        runner,
        "ensure_prepared_child_runtime",
        lambda **_kwargs: (_ for _ in ()).throw(
            runner.ChildRuntimeConfigError(
                "isolated execution requires explicit prepared child evidence"
            )
        ),
    )
    before_files = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and ".git" not in path.parts
    }
    before_status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    with pytest.raises(runner.ChildRuntimeConfigError, match="explicit prepared child evidence"):
        runner.run_next(plan.plan_path, execute=True, commit=True)

    after_files = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and ".git" not in path.parts
    }
    after_status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert after_files == before_files
    assert after_status == before_status


def test_isolated_final_only_runs_deterministic_gate_without_reviewer(tmp_path, monkeypatch):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex(tmp_path)
    plan = make_plan(tmp_path, isolated=False)
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    queue["units"][0].setdefault("execution_policy", {}).update(
        {"executor_adapter": "isolated-child", "review_policy": "final_only"}
    )
    plan.queue_json.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "CodexReadOnlyReviewer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("reviewer must not be constructed")),
    )

    result = runner.run_next(plan.plan_path, execute=True, commit=True, codex_command=str(fake_codex))

    assert result["action"] == "committed"
    evidence = json.loads(
        (plan.directory / "attempts" / "unit-001" / "attempt-0-review.json").read_text(encoding="utf-8")
    )
    assert evidence["review_mode"] == "deterministic_unit_gate"
    assert evidence["sessions_distinct"] is False


def test_isolated_release_recovers_after_prepare_before_commit_fault(tmp_path, monkeypatch):
    init_git_repo(tmp_path)
    plan = make_final_only_isolated_plan(tmp_path)
    fake_codex = write_fake_codex(tmp_path)
    original_commit = runner.commit_paths
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated release commit fault")
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(runner, "commit_paths", fail_once)
    with pytest.raises(OSError, match="simulated release commit fault"):
        runner.run_next(plan.plan_path, execute=True, commit=True, codex_command=str(fake_codex))

    monkeypatch.setattr(runner, "commit_paths", original_commit)
    deny_child_on_recovery(monkeypatch)
    result = runner.run_next(plan.plan_path, execute=True, commit=True)

    assert result["action"] == "committed"
    assert result["reconciled_release"] is True
    assert git_commit_count(tmp_path) == 2


def test_isolated_release_adopts_commit_after_completed_cas_fault(tmp_path, monkeypatch):
    init_git_repo(tmp_path)
    plan = make_final_only_isolated_plan(tmp_path)
    fake_codex = write_fake_codex(tmp_path)
    original_cas = runner.AttemptLedger.compare_and_set
    failed = False

    def fail_completed_once(self, revision, record):
        nonlocal failed
        if record.get("release_state") == "completed" and not failed:
            failed = True
            raise OSError("simulated release completion CAS fault")
        return original_cas(self, revision, record)

    monkeypatch.setattr(runner.AttemptLedger, "compare_and_set", fail_completed_once)
    with pytest.raises(OSError, match="simulated release completion CAS fault"):
        runner.run_next(plan.plan_path, execute=True, commit=True, codex_command=str(fake_codex))
    committed_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()

    monkeypatch.setattr(runner.AttemptLedger, "compare_and_set", original_cas)
    deny_child_on_recovery(monkeypatch)
    result = runner.run_next(plan.plan_path, execute=True, commit=True)

    assert result["commit"] == committed_head
    assert result["reconciled_release"] is True
    assert git_commit_count(tmp_path) == 2


def test_isolated_release_reconciles_completed_ledger_after_queue_fault(tmp_path, monkeypatch):
    init_git_repo(tmp_path)
    plan = make_final_only_isolated_plan(tmp_path)
    fake_codex = write_fake_codex(tmp_path)
    original_save = runner.plans.save_queue
    failed = False

    def fail_terminal_save(plan_dir, queue):
        nonlocal failed
        if any(unit.get("status") == "done" for unit in queue.get("units", [])) and not failed:
            failed = True
            raise OSError("simulated isolated final queue crash")
        return original_save(plan_dir, queue)

    monkeypatch.setattr(runner.plans, "save_queue", fail_terminal_save)
    with pytest.raises(OSError, match="simulated isolated final queue crash"):
        runner.run_next(plan.plan_path, execute=True, commit=True, codex_command=str(fake_codex))
    committed_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()

    deny_child_on_recovery(monkeypatch)
    result = runner.run_next(plan.plan_path, execute=True, commit=True)

    assert result["commit"] == committed_head
    assert result["reconciled_release"] is True
    assert git_commit_count(tmp_path) == 2
    assert "Completed commit unit 1." in (plan.directory / "log.md").read_text(encoding="utf-8")


def make_final_only_isolated_plan(tmp_path):
    plan = make_plan(tmp_path, isolated=False)
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    queue["units"][0].setdefault("execution_policy", {}).update(
        {"executor_adapter": "isolated-child", "review_policy": "final_only"}
    )
    plan.queue_json.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan


def deny_child_on_recovery(monkeypatch):
    monkeypatch.setattr(
        runner,
        "ensure_prepared_child_runtime",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("recovery must not start a child")),
    )


def git_commit_count(repo):
    return int(
        subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )


def test_queue_policy_inference_reads_commit_body_high_risk_signals(tmp_path):
    plan = make_plan(tmp_path)
    content = plan.plan_path.read_text(encoding="utf-8")
    plan.plan_path.write_text(
        content.replace("Allowed paths:", "This unit changes authentication behavior.\n\nAllowed paths:", 1),
        encoding="utf-8",
    )

    queue = plans.load_queue(plan.plan_path)[1]

    assert queue["units"][0]["execution_policy"]["effective_profile"] == "high_risk"


def test_source_route_persists_high_risk_signal_from_detailed_excerpt(tmp_path):
    source_path = tmp_path / "docs" / "plans" / "settings.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "\n".join(
            [
                "# Settings plan",
                "",
                "### Commit 1: Update settings",
                "",
                "- target files:",
                "  - `config/settings.yaml`",
                "- changes:",
                "  - Change OAuth authentication behavior.",
            ]
        ),
        encoding="utf-8",
    )

    plan = plans.create_plan_from_source(source_plan.resolve_source_plan(source_path, repo=tmp_path), repo=tmp_path)
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))

    assert queue["units"][0]["execution_policy"]["effective_profile"] == "high_risk"


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
prompt = sys.stdin.read()
output = pathlib.Path(args[args.index("--output-last-message") + 1])
if "Agent 3: Read-only Reviewer" in prompt:
    output.write_text(
        'INTERNAL_REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="clean"\\n'
        'COMMIT_UNIT_READY title="Fake implementation" summary="changed work.txt"\\n',
        encoding="utf-8",
    )
    print('{"session_id":"review-session"}')
else:
    work = pathlib.Path("work.txt")
    previous = work.read_text(encoding="utf-8") if work.exists() else ""
    work.write_text(previous + "implemented\\n", encoding="utf-8")
    output.write_text("implementation phase\\n", encoding="utf-8")
    print('{"session_id":"implementation-session"}')
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | 0o111)
    return fake_codex


def write_fake_codex_needs_work(tmp_path):
    fake_codex = tmp_path.parent / f"fake_codex_needs_work_{tmp_path.name}.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()
output = pathlib.Path(args[args.index("--output-last-message") + 1])
output.write_text(
    'INTERNAL_REVIEW_GATE status="needs_work" blockers=0 important=1 minor=0 reason="fake failure"\\n'
    'COMMIT_UNIT_NEEDS_WORK reason="fake failure"\\n',
    encoding="utf-8",
)
print('{"session_id":"review-session"}' if "Agent 3: Read-only Reviewer" in prompt else '{"session_id":"implementation-session"}')
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | 0o111)
    return fake_codex


def write_fake_codex_plain_implementer_hold(tmp_path):
    fake_codex = tmp_path.parent / f"fake_codex_plain_hold_{tmp_path.name}.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
output = pathlib.Path(args[args.index("--output-last-message") + 1])
output.write_text("status: `needs_work`\\n\\nGitHub preflight unavailable.\\n", encoding="utf-8")
print('{"session_id":"implementation-session"}')
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | 0o111)
    return fake_codex


def write_fake_codex_without_review_gate(tmp_path):
    fake_codex = tmp_path.parent / f"fake_codex_without_review_gate_{tmp_path.name}.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()
output = pathlib.Path(args[args.index("--output-last-message") + 1])
if "Agent 3: Read-only Reviewer" not in prompt:
    pathlib.Path("work.txt").write_text("implemented\\n", encoding="utf-8")
    output.write_text("implementation phase\\n", encoding="utf-8")
    print('{"session_id":"implementation-session"}')
else:
    output.write_text('COMMIT_UNIT_READY title="Ungated" summary="missing review evidence"\\n', encoding="utf-8")
    print('{"session_id":"review-session"}')
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | 0o111)
    return fake_codex


def write_fake_codex_with_incomplete_review_gate(tmp_path):
    fake_codex = tmp_path.parent / f"fake_codex_with_incomplete_review_gate_{tmp_path.name}.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()
output = pathlib.Path(args[args.index("--output-last-message") + 1])
if "Agent 3: Read-only Reviewer" not in prompt:
    pathlib.Path("work.txt").write_text("implemented\\n", encoding="utf-8")
    output.write_text("implementation phase\\n", encoding="utf-8")
    print('{"session_id":"implementation-session"}')
else:
    output.write_text(
        'INTERNAL_REVIEW_GATE status="pass" reason="counts omitted"\\n'
        'COMMIT_UNIT_READY title="Incomplete gate" summary="missing counts"\\n',
        encoding="utf-8",
    )
    print('{"session_id":"review-session"}')
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | 0o111)
    return fake_codex


def write_fake_codex_outside_scope(tmp_path):
    fake_codex = tmp_path.parent / f"fake_codex_outside_scope_{tmp_path.name}.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()
output = pathlib.Path(args[args.index("--output-last-message") + 1])
if "Agent 3: Read-only Reviewer" not in prompt:
    pathlib.Path("work.txt").write_text("allowed\\n", encoding="utf-8")
    pathlib.Path("outside.txt").write_text("not allowed\\n", encoding="utf-8")
    output.write_text("implementation phase\\n", encoding="utf-8")
    print('{"session_id":"implementation-session"}')
else:
    output.write_text(
        'INTERNAL_REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="clean"\\n'
        'COMMIT_UNIT_READY title="Out of scope" summary="changed two paths"\\n',
        encoding="utf-8",
    )
    print('{"session_id":"review-session"}')
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | 0o111)
    return fake_codex


def write_fake_codex_that_commits(tmp_path):
    fake_codex = tmp_path.parent / f"fake_codex_that_commits_{tmp_path.name}.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import pathlib
import subprocess
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()
output = pathlib.Path(args[args.index("--output-last-message") + 1])
if "Agent 3: Read-only Reviewer" not in prompt:
    pathlib.Path("work.txt").write_text("child commit\\n", encoding="utf-8")
    subprocess.run(["git", "add", "work.txt"], check=True)
    subprocess.run(
        ["git", "-c", "user.email=codex-flow@example.com", "-c", "user.name=Codex Flow", "commit", "-m", "unexpected child commit"],
        check=True,
        capture_output=True,
    )
    output.write_text("implementation phase\\n", encoding="utf-8")
    print('{"session_id":"implementation-session"}')
else:
    output.write_text(
        'INTERNAL_REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="clean"\\n'
        'COMMIT_UNIT_READY title="Child committed" summary="unexpected commit"\\n',
        encoding="utf-8",
    )
    print('{"session_id":"review-session"}')
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | 0o111)
    return fake_codex


def write_fake_codex_needs_work_then_ready(tmp_path):
    fake_codex = tmp_path.parent / f"fake_codex_repair_{tmp_path.name}.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()
output = pathlib.Path(args[args.index("--output-last-message") + 1])
if "Agent 3: Read-only Reviewer" not in prompt:
    work = pathlib.Path("work.txt")
    previous = work.read_text(encoding="utf-8") if work.exists() else ""
    line = "repair\\n" if "Repair attempt:" in prompt else "initial\\n"
    work.write_text(previous + line, encoding="utf-8")
    output.write_text("implementation phase\\n", encoding="utf-8")
    print('{"session_id":"implementation-session"}')
else:
    counter = pathlib.Path(__file__).with_suffix(".count")
    count = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    count += 1
    counter.write_text(str(count), encoding="utf-8")
    if count == 1:
        output.write_text(
            'INTERNAL_REVIEW_GATE status="needs_work" blockers=0 important=1 minor=0 reason="first review failed"\\n'
            'COMMIT_UNIT_NEEDS_WORK reason="first review failed"\\n',
            encoding="utf-8",
        )
    else:
        output.write_text(
            'INTERNAL_REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="clean"\\n'
            'COMMIT_UNIT_READY title="Fake repair" summary="repair succeeded"\\n',
            encoding="utf-8",
        )
    print('{"session_id":"review-session"}')
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | 0o111)
    return fake_codex


def write_fake_codex_conflicting_review_protocol(tmp_path):
    fake_codex = tmp_path.parent / f"fake_codex_conflicting_review_{tmp_path.name}.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()
output = pathlib.Path(args[args.index("--output-last-message") + 1])
if "Agent 3: Read-only Reviewer" not in prompt:
    work = pathlib.Path("work.txt")
    previous = work.read_text(encoding="utf-8") if work.exists() else ""
    work.write_text(previous + "implemented\\n", encoding="utf-8")
    output.write_text("implementation phase\\n", encoding="utf-8")
else:
    output.write_text(
        'INTERNAL_REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="clean"\\n'
        'COMMIT_UNIT_READY title="Fake" summary="ready"\\n'
        'COMMIT_UNIT_NEEDS_WORK reason=""\\n',
        encoding="utf-8",
    )
print('{"session_id":"review-session"}' if "Agent 3: Read-only Reviewer" in prompt else '{"session_id":"implementation-session"}')
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | 0o111)
    return fake_codex


def write_fake_codex_review_gate_blocks_then_passes(tmp_path):
    fake_codex = tmp_path.parent / f"fake_codex_review_gate_{tmp_path.name}.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()
output = pathlib.Path(args[args.index("--output-last-message") + 1])
work = pathlib.Path("work.txt")
if "Agent 3: Read-only Reviewer" not in prompt:
    previous = work.read_text(encoding="utf-8") if work.exists() else ""
    line = "gate-repair\\n" if "Repair attempt:" in prompt else "gate-initial\\n"
    work.write_text(previous + line, encoding="utf-8")
    output.write_text("implementation phase\\n", encoding="utf-8")
    print('{"session_id":"implementation-session"}')
else:
    counter = pathlib.Path(__file__).with_suffix(".count")
    count = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    count += 1
    counter.write_text(str(count), encoding="utf-8")
    if count == 1:
        output.write_text(
            'INTERNAL_REVIEW_GATE status="pass" blockers=0 important=1 minor=0 reason="important issue remains"\\n'
            'COMMIT_UNIT_READY title="Fake gated implementation" summary="ready but gate blocks"\\n',
            encoding="utf-8",
        )
    else:
        output.write_text(
            'INTERNAL_REVIEW_GATE status="pass" blockers=0 important=0 minor=1 reason="minor follow-up only"\\n'
            'COMMIT_UNIT_READY title="Fake gated repair" summary="gate passed"\\n',
            encoding="utf-8",
        )
    print('{"session_id":"review-session"}')
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | 0o111)
    return fake_codex


def write_fake_codex_dirty_needs_work(tmp_path):
    fake_codex = tmp_path.parent / f"fake_codex_dirty_needs_work_{tmp_path.name}.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()
output = pathlib.Path(args[args.index("--output-last-message") + 1])
if "Agent 3: Read-only Reviewer" not in prompt:
    work = pathlib.Path("work.txt")
    previous = work.read_text(encoding="utf-8") if work.exists() else ""
    line = "partial-repair\\n" if "Repair attempt:" in prompt else "partial-initial\\n"
    work.write_text(previous + line, encoding="utf-8")
    output.write_text("implementation phase\\n", encoding="utf-8")
else:
    output.write_text(
        'INTERNAL_REVIEW_GATE status="needs_work" blockers=0 important=1 minor=0 reason="still failing"\\n'
        'COMMIT_UNIT_NEEDS_WORK reason="still failing"\\n',
        encoding="utf-8",
    )
print('{"session_id":"review-session"}' if "Agent 3: Read-only Reviewer" in prompt else '{"session_id":"implementation-session"}')
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | 0o111)
    return fake_codex


def write_fake_codex_timeout(tmp_path):
    fake_codex = tmp_path.parent / f"fake_codex_timeout_{tmp_path.name}.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import time

time.sleep(5)
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | 0o111)
    return fake_codex


def write_fake_codex_review_failure(tmp_path):
    fake_codex = tmp_path.parent / f"fake_codex_review_failure_{tmp_path.name}.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()
output = pathlib.Path(args[args.index("--output-last-message") + 1])
if "Agent 3: Read-only Reviewer" in prompt:
    raise SystemExit(7)
pathlib.Path("work.txt").write_text("implemented\\n", encoding="utf-8")
output.write_text("implementation phase\\n", encoding="utf-8")
print('{"session_id":"implementation-session"}')
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | 0o111)
    return fake_codex


def write_fake_codex_resume_repair_ready(tmp_path):
    fake_codex = tmp_path.parent / f"fake_codex_resume_repair_{tmp_path.name}.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()
output = pathlib.Path(args[args.index("--output-last-message") + 1])
work = pathlib.Path("work.txt")
if "Agent 3: Read-only Reviewer" not in prompt:
    if "Repair attempt:" in prompt and "Previous needs_work reason: still failing" in prompt and work.exists():
        work.write_text(work.read_text(encoding="utf-8") + "final-repair\\n", encoding="utf-8")
    output.write_text("implementation phase\\n", encoding="utf-8")
else:
    if work.exists() and "partial-initial" in work.read_text(encoding="utf-8") and "final-repair" in work.read_text(encoding="utf-8"):
        output.write_text(
            'INTERNAL_REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="clean"\\n'
            'COMMIT_UNIT_READY title="Fake resumed repair" summary="resumed repair succeeded"\\n',
            encoding="utf-8",
        )
    else:
        output.write_text(
            'INTERNAL_REVIEW_GATE status="needs_work" blockers=0 important=1 minor=0 reason="resume context missing"\\n'
            'COMMIT_UNIT_NEEDS_WORK reason="resume context missing"\\n',
            encoding="utf-8",
        )
print('{"session_id":"review-session"}' if "Agent 3: Read-only Reviewer" in prompt else '{"session_id":"implementation-session"}')
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
    prompt_text = result["prompt_path"].read_text(encoding="utf-8")
    assert "## Skill Routing Manifest" in prompt_text
    assert "Required skills: `요청개선`, `plan-first-implementation`" in prompt_text
    assert "A separate fresh read-only reviewer runs after implementation." in prompt_text
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert queue["units"][0]["status"] == "prompted"
    assert queue["units"][0]["prompt_path"] == "prompts/unit-001.md"


def test_run_next_prompt_includes_full_plan_context(tmp_path):
    plan = make_plan(tmp_path)
    plan_text = plan.plan_path.read_text(encoding="utf-8")
    plan.plan_path.write_text(
        plan_text
        + "\n## Custom Implementation Detail\n\n"
        + "- Preserve this plan-specific instruction in the implementer prompt.\n",
        encoding="utf-8",
    )

    result = runner.run_next(plan.plan_path)

    prompt_text = result["prompt_path"].read_text(encoding="utf-8")
    assert "## Full Plan Context" in prompt_text
    assert "Read this plan as the source of truth" in prompt_text
    assert "Preserve this plan-specific instruction in the implementer prompt." in prompt_text
    assert "## Selected Commit Unit" in prompt_text


def test_run_next_repairs_existing_plan_manifest_before_prompt(tmp_path):
    plan = make_plan(tmp_path)
    plan_text = plan.plan_path.read_text(encoding="utf-8").replace("`요청개선`, `plan-first-implementation`", "`요청개선`")
    plan.plan_path.write_text(plan_text, encoding="utf-8")
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    queue["units"][0]["required_skills"] = ["요청개선"]
    plan.queue_json.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = runner.run_next(plan.plan_path)

    prompt_text = result["prompt_path"].read_text(encoding="utf-8")
    repaired_plan_text = plan.plan_path.read_text(encoding="utf-8")
    repaired_queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert "Required skills: `요청개선`, `plan-first-implementation`" in prompt_text
    assert "| Commit 1: 근거 수집과 범위 잠금 | `요청개선`, `plan-first-implementation` |" in repaired_plan_text
    assert repaired_queue["units"][0]["required_skills"] == ["요청개선", "plan-first-implementation"]


def test_run_all_respects_max_units(tmp_path):
    plan = make_plan(tmp_path)

    results = runner.run_all(plan.plan_path, max_units=2)

    assert len(results) == 2
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert [unit["status"] for unit in queue["units"]] == ["prompted", "prompted"]
    first_prompt = (plan.directory / queue["units"][0]["prompt_path"]).read_text(encoding="utf-8")
    second_prompt = (plan.directory / queue["units"][1]["prompt_path"]).read_text(encoding="utf-8")
    assert "Required skills: `요청개선`, `plan-first-implementation`" in first_prompt
    assert "Required skills: `plan-first-implementation`, `mission-completion-harness`" in second_prompt
    assert "frontend/**" in second_prompt


def test_execute_after_prompted_unit_still_runs_same_commit_unit(tmp_path):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex(tmp_path)
    plan = make_plan(tmp_path)
    prompted = runner.run_next(plan.plan_path)

    result = runner.run_next(plan.plan_path, execute=True, commit=True, codex_command=str(fake_codex))

    assert prompted["unit"]["id"] == "unit-001"
    assert result["unit"]["id"] == "unit-001"
    assert result["action"] == "committed"
    assert "Completed commit unit 1." in (plan.directory / "log.md").read_text(encoding="utf-8")


def test_run_all_execute_defaults_to_until_complete(tmp_path):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex(tmp_path)
    plan = make_plan(tmp_path)

    results = runner.run_all(plan.plan_path, execute=True, commit=True, codex_command=str(fake_codex))

    assert len(results) == 2
    assert [result["unit"]["id"] for result in results] == ["unit-001", "unit-002"]
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert all(unit["status"] == "done" for unit in queue["units"])


def test_morning_brief_review_and_pr_dry_run(tmp_path):
    plan = make_plan(tmp_path)
    runner.run_next(plan.plan_path)

    brief = briefs.write_morning_brief(tmp_path)
    review = briefs.write_review(plan.plan_path)
    pr = briefs.write_pr_dry_run(plan.plan_path)

    assert brief.exists()
    assert review.exists()
    assert pr.exists()
    assert "Required skills from the Skill Routing Manifest" in review.read_text(encoding="utf-8")
    assert "## Skill Routing Manifest" in pr.read_text(encoding="utf-8")
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


def test_run_next_preserves_plain_implementer_needs_work_without_marking_done(tmp_path):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex_plain_implementer_hold(tmp_path)
    plan = make_plan(tmp_path)

    result = runner.run_next(plan.plan_path, execute=True, commit=True, codex_command=str(fake_codex))

    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert result["action"] == "needs_work"
    assert result["commit"] == ""
    assert queue["units"][0]["status"] == "needs_work"
    assert "GitHub preflight unavailable" in queue["units"][0]["last_needs_work_reason"]
    assert subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.count("\n") == 1


def test_implementer_needs_work_parser_accepts_korean_status_label():
    assert runner.implementer_needs_work_reason("상태: `needs_work`\n\nGitHub 연결 실패") == (
        "상태: `needs_work` GitHub 연결 실패"
    )


def test_run_next_refuses_commit_without_internal_review_evidence(tmp_path):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex_without_review_gate(tmp_path)
    plan = make_plan(tmp_path)

    result = runner.run_next(plan.plan_path, execute=True, commit=True, codex_command=str(fake_codex))

    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert result["action"] == "needs_work"
    assert "internal review evidence" in result["reason"]
    assert result["commit"] == ""
    assert queue["units"][0]["status"] == "needs_work"
    assert subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.count("\n") == 1


def test_run_next_refuses_commit_with_incomplete_review_gate(tmp_path):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex_with_incomplete_review_gate(tmp_path)
    plan = make_plan(tmp_path)

    result = runner.run_next(plan.plan_path, execute=True, commit=True, codex_command=str(fake_codex))

    assert result["action"] == "needs_work"
    assert "incomplete internal review evidence" in result["reason"]
    assert result["commit"] == ""
    assert subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.count("\n") == 1


def test_run_next_refuses_unit_commit_with_paths_outside_allowed_scope(tmp_path):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex_outside_scope(tmp_path)
    plan = make_plan(tmp_path)
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    queue["units"][0]["allowed_paths"] = ["work.txt"]
    plan.queue_json.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = runner.run_next(plan.plan_path, execute=True, commit=True, codex_command=str(fake_codex))

    persisted = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert result["action"] == "needs_work"
    assert result["out_of_scope_paths"] == ["outside.txt"]
    assert result["commit"] == ""
    assert persisted["units"][0]["status"] == "needs_work"
    assert subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.count("\n") == 1


def test_run_next_preserves_plan_skills_and_binds_internal_review_attempt(tmp_path, monkeypatch):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex(tmp_path)
    plan = make_plan(tmp_path)
    captured: list[tuple[str, ...]] = []

    def verify(value, **kwargs):
        captured.append(tuple(kwargs["required_skills"]))
        return value

    monkeypatch.setattr(runner, "verify_child_attestation", verify)

    result = runner.run_next(plan.plan_path, execute=True, commit=False, codex_command=str(fake_codex))

    evidence = json.loads(
        (plan.directory / "attempts" / "unit-001" / "attempt-0-review.json").read_text(encoding="utf-8")
    )
    assert result["action"] == "done"
    assert captured[0] == ("요청개선", "plan-first-implementation")
    assert evidence["review_mode"] == "fresh_read_only"
    assert evidence["sessions_distinct"] is True
    assert evidence["implementation_session_sha256"]
    assert evidence["reviewer_session_sha256"]
    assert "implementation_session_id" not in evidence
    assert "reviewer_session_id" not in evidence
    assert evidence["head_unchanged"] is True
    assert evidence["full_diff_digest_unchanged"] is True
    assert "review_skill" not in evidence
    assert evidence["child_attestation"] == result["unit"]["child_attestation"]


def test_run_next_refuses_done_state_when_child_moves_head(tmp_path, monkeypatch):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex_that_commits(tmp_path)
    plan = make_plan(tmp_path)

    class CommittingImplementer:
        def __init__(self, *_args, **_kwargs):
            pass

        def implement(self, input_data, **_kwargs):
            (input_data.repo / "work.txt").write_text("child commit\n", encoding="utf-8")
            subprocess.run(["git", "add", "work.txt"], cwd=input_data.repo, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.email=codex-flow@example.com",
                    "-c",
                    "user.name=Codex Flow",
                    "commit",
                    "-m",
                    "unexpected child commit",
                ],
                cwd=input_data.repo,
                check=True,
                capture_output=True,
            )
            return SimpleNamespace(session_id="implementation-session", implementation_message="implementation phase")

    monkeypatch.setattr(runner, "CodexImplementerAgent", CommittingImplementer)

    result = runner.run_next(plan.plan_path, execute=True, commit=True, codex_command=str(fake_codex))

    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert result["action"] == "needs_work"
    assert "moved HEAD" in result["reason"]
    assert result["commit"] == ""
    assert queue["units"][0]["status"] == "needs_work"
    assert subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.count("\n") == 2


def test_run_next_cli_execute_commits_by_default(tmp_path, capsys):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex(tmp_path)
    plan = make_plan(tmp_path)

    status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "run-next",
            "--plan",
            str(plan.plan_path),
            "--codex-command",
            str(fake_codex),
        ]
    )

    output = capsys.readouterr().out
    assert status == 0
    assert "action: committed" in output
    assert "commit:" in output
    assert subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.count("\n") == 2


def test_run_next_cli_preview_keeps_prompt_only_escape_hatch(tmp_path, capsys):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex(tmp_path)
    plan = make_plan(tmp_path)

    status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "run-next",
            "--plan",
            str(plan.plan_path),
            "--preview",
            "--codex-command",
            str(fake_codex),
        ]
    )

    output = capsys.readouterr().out
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert status == 0
    assert "status: prompted" in output
    assert queue["units"][0]["status"] == "prompted"
    assert not (tmp_path / "work.txt").exists()
    assert subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.count("\n") == 1


def test_run_next_cli_no_commit_keeps_escape_hatch(tmp_path, capsys):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex(tmp_path)
    plan = make_plan(tmp_path)

    status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "run-next",
            "--plan",
            str(plan.plan_path),
            "--no-commit",
            "--codex-command",
            str(fake_codex),
        ]
    )

    output = capsys.readouterr().out
    assert status == 0
    assert "action: done" in output
    assert "commit:" not in output
    assert subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.count("\n") == 1


def test_run_all_cli_holds_merge_until_final_gate_exists(tmp_path, capsys):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex(tmp_path)
    plan = make_plan(tmp_path)

    status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "run-all",
            "--plan",
            str(plan.plan_path),
            "--codex-command",
            str(fake_codex),
        ]
    )

    output = capsys.readouterr().out
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert status == 1
    assert "units_processed: 2" in output
    assert "final_gate: final gate evidence is missing" in output
    assert all(unit["status"] == "done" for unit in queue["units"])
    assert subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.count("\n") == 3
    assert subprocess.run(["git", "branch", "--show-current"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip().startswith("codex/")
    assert subprocess.run(["git", "branch", "--list", "codex/*"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()


def test_run_next_needs_work_returns_nonzero(tmp_path, capsys):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex_needs_work(tmp_path)
    plan = make_plan(tmp_path)

    status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "run-next",
            "--plan",
            str(plan.plan_path),
            "--codex-command",
            str(fake_codex),
        ]
    )

    output = capsys.readouterr().out
    assert status == 1
    assert "action: needs_work" in output


def test_run_next_auto_resolve_stops_after_repair_budget(tmp_path, capsys):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex_needs_work(tmp_path)
    plan = make_plan(tmp_path)

    status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "run-next",
            "--plan",
            str(plan.plan_path),
            "--auto-resolve",
            "--codex-command",
            str(fake_codex),
        ]
    )

    output = capsys.readouterr().out
    log = (plan.directory / "log.md").read_text(encoding="utf-8")
    assert status == 1
    assert "action: needs_work" in output
    assert "repair_attempts" not in output
    assert "Repair attempt" not in log
    assert "Commit unit 1 needs_work: fake failure" in log


def test_run_next_does_not_retry_conflicting_review_protocol(tmp_path, capsys):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex_conflicting_review_protocol(tmp_path)
    plan = make_plan(tmp_path)

    status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "run-next",
            "--plan",
            str(plan.plan_path),
            "--auto-resolve",
            "--codex-command",
            str(fake_codex),
        ]
    )

    output = capsys.readouterr().out
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    log = (plan.directory / "log.md").read_text(encoding="utf-8")
    assert status == 1
    assert "action: needs_work" in output
    assert queue["units"][0]["status"] == "needs_work"
    assert queue["units"][0]["repair_attempts"] == 0
    assert "exactly one" in queue["units"][0]["last_needs_work_reason"]
    assert "Repair attempt" not in log


def test_run_all_auto_resolve_does_not_retry_needs_work(tmp_path, capsys):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex_needs_work_then_ready(tmp_path)
    plan = make_plan(tmp_path)

    status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "run-all",
            "--plan",
            str(plan.plan_path),
            "--auto-resolve",
            "--codex-command",
            str(fake_codex),
        ]
    )

    output = capsys.readouterr().out
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    log = (plan.directory / "log.md").read_text(encoding="utf-8")
    assert status == 1
    assert "units_processed: 1" in output
    assert "repair_attempts" not in output
    assert queue["units"][0]["status"] == "needs_work"
    assert "Repair attempt" not in log


def test_review_gate_holds_important_findings_without_automatic_repair(tmp_path, capsys):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex_review_gate_blocks_then_passes(tmp_path)
    plan = make_plan(tmp_path)

    status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "run-next",
            "--plan",
            str(plan.plan_path),
            "--auto-resolve",
            "--codex-command",
            str(fake_codex),
        ]
    )

    output = capsys.readouterr().out
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    log = (plan.directory / "log.md").read_text(encoding="utf-8")
    attempt_0 = json.loads((plan.directory / "attempts" / "unit-001" / "attempt-0-review.json").read_text(encoding="utf-8"))
    assert attempt_0["ledger_revision"] >= 1
    assert status == 1
    assert "action: needs_work" in output
    assert "repair_attempts" not in output
    assert queue["units"][0]["status"] == "needs_work"
    assert queue["units"][0]["review_gate"]["important"] == 1
    assert attempt_0["status"] == "needs_work"
    assert attempt_0["review_gate"]["important"] == 1
    assert "Review gate for commit unit 1 attempt 0: review_gate=pass score=80 blockers=0 important=1 minor=0" in log
    assert "attempt 1" not in log


def test_run_next_auto_resolve_resumes_failed_needs_work_with_partial_changes(tmp_path, capsys):
    init_git_repo(tmp_path)
    failing_codex = write_fake_codex_dirty_needs_work(tmp_path)
    repairing_codex = write_fake_codex_resume_repair_ready(tmp_path)
    plan = make_plan(tmp_path)

    first_status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "run-next",
            "--plan",
            str(plan.plan_path),
            "--auto-resolve",
            "--codex-command",
            str(failing_codex),
        ]
    )

    first_output = capsys.readouterr().out
    first_queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    assert first_status == 1
    assert "action: needs_work" in first_output
    assert first_queue["units"][0]["status"] == "needs_work"
    assert first_queue["units"][0]["changed_paths"] == ["work.txt"]
    assert "partial-initial" in (tmp_path / "work.txt").read_text(encoding="utf-8")

    second_status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "run-next",
            "--plan",
            str(plan.plan_path),
            "--auto-resolve",
            "--codex-command",
            str(repairing_codex),
        ]
    )

    second_output = capsys.readouterr().out
    second_queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    log = (plan.directory / "log.md").read_text(encoding="utf-8")
    stash_list = subprocess.run(["git", "stash", "list"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    assert second_status == 0
    assert "action: committed" in second_output
    assert "repair_attempts: 1" in second_output
    assert second_queue["units"][0]["status"] == "done"
    assert second_queue["units"][0]["changed_paths"] == ["work.txt"]
    assert "Preserved previous needs_work changes for unit-001: work.txt" in log
    assert "Repair attempt 1/1" in log
    assert "codex-flow auto-shelve" not in stash_list
    assert "final-repair" in (tmp_path / "work.txt").read_text(encoding="utf-8")


def test_run_next_auto_resolve_refuses_dirty_in_place_repository_without_stash(tmp_path):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex(tmp_path)
    (tmp_path / "dirty-note.md").write_text("preserve me\n", encoding="utf-8")

    plan = make_plan(tmp_path)
    with pytest.raises(SystemExit, match="requires a clean repository"):
        runner.run_next(
            plan.plan_path,
            execute=True,
            commit=True,
            codex_command=str(fake_codex),
            auto_resolve=True,
        )

    assert (tmp_path / "dirty-note.md").read_text(encoding="utf-8") == "preserve me\n"
    stash_list = subprocess.run(["git", "stash", "list"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    assert stash_list == ""


def test_run_next_timeout_marks_unit_needs_work_with_diagnostics(tmp_path, capsys):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex_timeout(tmp_path)
    plan = make_plan(tmp_path)

    status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "run-next",
            "--plan",
            str(plan.plan_path),
            "--auto-resolve",
            "--codex-command",
            str(fake_codex),
            "--codex-timeout-seconds",
            "1",
        ]
    )

    output = capsys.readouterr().out
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    diagnostic_path = plan.directory / queue["units"][0]["diagnostic_path"]
    assert status == 1
    assert "action: needs_work" in output
    assert "diagnostic_path:" in output
    assert queue["units"][0]["status"] == "needs_work"
    assert "timed out" in queue["units"][0]["last_needs_work_reason"]
    assert (diagnostic_path / "prompt.json").exists()
    assert (diagnostic_path / "metadata.json").exists()


def test_run_next_holds_review_process_failure_with_review_phase_and_no_commit(tmp_path):
    init_git_repo(tmp_path)
    fake_codex = write_fake_codex_review_failure(tmp_path)
    plan = make_plan(tmp_path)

    result = runner.run_next(plan.plan_path, execute=True, commit=True, codex_command=str(fake_codex))

    evidence = json.loads(
        (plan.directory / "attempts" / "unit-001" / "attempt-0-review.json").read_text(encoding="utf-8")
    )
    metadata = json.loads(
        (plan.directory / "attempts" / "unit-001" / "attempt-0" / "review" / "metadata.json").read_text(encoding="utf-8")
    )
    assert result["action"] == "needs_work"
    assert "invariants preserved" in result["reason"]
    assert evidence["process_error"] == "CodexExecFailure"
    assert evidence["head_unchanged"] is True
    assert evidence["full_diff_digest_unchanged"] is True
    assert metadata["phase"] == "review"
    assert subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.count("\n") == 1


def test_route_queues_when_pr_lock_is_active(tmp_path, capsys):
    source = tmp_path / "docs" / "plans" / "locked-source.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Locked Source\n\n### Commit 1: Wait\n\n- Wait for PR lock.\n", encoding="utf-8")
    pr.write_pr_lock(tmp_path, "codex/open", "https://github.com/example/repo/pull/1", "reviewing")

    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])

    output = capsys.readouterr().out
    assert status == 0
    assert "queued source plan due to active PR lock" in output
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
    assert subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.count("\n") == 3


def test_merge_auto_resolve_executes_units_but_holds_without_final_gate(tmp_path, capsys):
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
            "--codex-command",
            str(fake_codex),
        ]
    )

    output = capsys.readouterr().out
    assert status == 2
    assert "auto_resolve_units:" in output
    assert "final_gate: final gate evidence is missing" in output
    assert subprocess.run(["git", "branch", "--show-current"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip().startswith("codex/")
