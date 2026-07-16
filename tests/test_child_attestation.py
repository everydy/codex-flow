from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from codex_flow import plans, runner, tickets
from codex_flow.codex_cli import (
    CHILD_MANIFEST_ENV,
    ChildAttestationError,
    attestation_digest,
    consume_attestation_nonce,
    generate_child_attestation,
    load_child_closure_manifest,
    path_tree_hash,
    verify_child_attestation,
)


NOW = datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc)
REQUIRED_SKILLS = ("plan-first-implementation", "review-all-in-one")


def write_child_runtime(
    tmp_path: Path,
    monkeypatch,
    *,
    skill_ids=REQUIRED_SKILLS,
    discovered_ids=None,
    full_agent=False,
    plugin_relative="plugins/codex-flow-runtime",
    plugin_skill_ids=(),
    external_skill_ids=(),
):
    if discovered_ids is None:
        discovered_ids = skill_ids
    child_home = tmp_path.parent / f"{tmp_path.name}-child-home"
    child_home.mkdir()
    (child_home / "config.toml").write_text("[skills.bundled]\nenabled = false\n", encoding="utf-8")
    skill_entries = []
    for skill_id in skill_ids:
        skill_dir = child_home / "skills" / skill_id
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"# {skill_id}\n", encoding="utf-8")
        skill_entries.append(
            {"id": skill_id, "path": f"skills/{skill_id}", "sha256": path_tree_hash(skill_dir)}
        )
    plugin_entries = []
    discovered_paths = {}
    for skill_id in skill_ids:
        discovered_paths[skill_id] = str(child_home / "skills" / skill_id / "SKILL.md")
    if plugin_relative is not None:
        plugin_dir = child_home / plugin_relative
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text('{"name":"codex-flow-runtime"}\n', encoding="utf-8")
        for skill_id in plugin_skill_ids:
            plugin_skill = plugin_dir / "skills" / skill_id.rsplit(":", 1)[-1]
            plugin_skill.mkdir(parents=True)
            (plugin_skill / "SKILL.md").write_text(f"# {skill_id}\n", encoding="utf-8")
            discovered_paths[skill_id] = str(plugin_skill / "SKILL.md")
        plugin_entries.append(
            {
                "id": "codex-flow-runtime",
                "path": plugin_relative,
                "sha256": path_tree_hash(plugin_dir),
                "skill_ids": list(plugin_skill_ids),
            }
        )
    external_root = tmp_path.parent / f"{tmp_path.name}-external-skills"
    for index, skill_id in enumerate(external_skill_ids):
        external_skill = external_root / str(index)
        external_skill.mkdir(parents=True)
        (external_skill / "SKILL.md").write_text(f"# {skill_id}\n", encoding="utf-8")
        discovered_paths[skill_id] = str(external_skill / "SKILL.md")
    for skill_id in discovered_ids:
        discovered_paths.setdefault(skill_id, str(external_root / "unexpected" / "SKILL.md"))
    manifest_path = child_home / "child-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "skills": skill_entries,
                "plugins": plugin_entries,
                "external_skill_ids": list(external_skill_ids),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    fake_codex = tmp_path.parent / f"{tmp_path.name}-fake-codex.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import re
import sys

args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 9.9.9")
    raise SystemExit(0)
if args == ["app-server", "--listen", "stdio://"]:
    discovered_paths = json.loads(os.environ["DISCOVERED_PATHS"])
    for line in sys.stdin:
        request = json.loads(line)
        if request["method"] == "initialize":
            response = {"id": request["id"], "result": {"codexHome": os.environ["CODEX_HOME"]}}
        elif request["method"] == "skills/list":
            cwd = request["params"]["cwds"][0]
            skills = [
                {"name": skill_id, "path": discovered_paths[skill_id], "enabled": True}
                for skill_id in os.environ["DISCOVERED_IDS"].split(",")
                if skill_id
            ]
            response = {
                "id": request["id"],
                "result": {"data": [{"cwd": cwd, "errors": [], "skills": skills}]},
            }
        else:
            response = {"id": request["id"], "error": {"message": "unexpected method"}}
        print(json.dumps(response), flush=True)
    raise SystemExit(0)
prompt = sys.stdin.read()
output = pathlib.Path(args[args.index("--output-last-message") + 1])
if os.environ.get("FULL_AGENT") == "1":
    if "resume" in args:
        output.write_text(
            'REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="clean"\\n'
            'COMMIT_UNIT_READY title="Attested" summary="implemented"\\n',
            encoding="utf-8",
        )
    else:
        pathlib.Path("work.txt").write_text("implemented\\n", encoding="utf-8")
        output.write_text("implementation phase\\n", encoding="utf-8")
    print('{"session_id":"implementation-session"}')
else:
    raise SystemExit("unexpected non-discovery invocation")
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    monkeypatch.setenv("CODEX_FLOW_CHILD_HOME", str(child_home))
    monkeypatch.setenv(CHILD_MANIFEST_ENV, str(manifest_path))
    monkeypatch.setenv("DISCOVERED_IDS", ",".join(discovered_ids))
    monkeypatch.setenv("DISCOVERED_PATHS", json.dumps(discovered_paths))
    monkeypatch.setenv("FULL_AGENT", "1" if full_agent else "0")
    return child_home, manifest_path, fake_codex


def resign(attestation, **changes):
    changed = replace(attestation, **changes)
    return replace(changed, binding_sha256=attestation_digest(changed))


def write_app_server_only_codex(tmp_path: Path, child_home: Path) -> Path:
    fake_codex = tmp_path / "fake-app-server-codex.py"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 9.9.9")
    raise SystemExit(0)
if args != ["app-server", "--listen", "stdio://"]:
    raise SystemExit(42)
for line in sys.stdin:
    request = json.loads(line)
    if request["method"] == "initialize":
        response = {"id": request["id"], "result": {"codexHome": os.environ["CODEX_HOME"]}}
    elif request["method"] == "skills/list":
        cwd = request["params"]["cwds"][0]
        root = pathlib.Path(os.environ["CODEX_HOME"])
        skills = [
            {
                "name": skill_id,
                "path": str(root / "skills" / skill_id / "SKILL.md"),
                "enabled": True,
            }
            for skill_id in os.environ["DISCOVERED_IDS"].split(",")
            if skill_id
        ]
        response = {
            "id": request["id"],
            "result": {"data": [{"cwd": cwd, "errors": [], "skills": skills}]},
        }
    else:
        response = {"id": request["id"], "error": {"message": "unexpected method"}}
    print(json.dumps(response), flush=True)
raise SystemExit(int(os.environ.get("APP_SERVER_EXIT", "0")))
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    return fake_codex


def test_fresh_child_attestation_uses_app_server_inventory_not_model_self_report(
    tmp_path, monkeypatch
):
    child_home, _, _ = write_child_runtime(tmp_path, monkeypatch)
    fake_codex = write_app_server_only_codex(tmp_path, child_home)
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# approved plan\n", encoding="utf-8")

    attestation = generate_child_attestation(
        repo=tmp_path,
        plan_path=plan_path,
        command=str(fake_codex),
        extra_args=[],
        now=NOW,
    )

    assert attestation.loaded_skills == REQUIRED_SKILLS
    assert attestation.discovery_command == (
        str(fake_codex),
        "app-server",
        "--listen",
        "stdio://",
    )


def test_fresh_child_attestation_rejects_app_server_failure_after_inventory(
    tmp_path, monkeypatch
):
    child_home, _, _ = write_child_runtime(tmp_path, monkeypatch)
    fake_codex = write_app_server_only_codex(tmp_path, child_home)
    monkeypatch.setenv("APP_SERVER_EXIT", "7")
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# approved plan\n", encoding="utf-8")

    with pytest.raises(ChildAttestationError, match="app-server exited 7"):
        generate_child_attestation(
            repo=tmp_path,
            plan_path=plan_path,
            command=str(fake_codex),
            extra_args=[],
            now=NOW,
        )


def test_fresh_child_discovery_attestation_verifies_exact_closure(tmp_path, monkeypatch):
    child_home, manifest_path, fake_codex = write_child_runtime(tmp_path, monkeypatch)
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# approved plan\n", encoding="utf-8")
    ledger = tmp_path / "used-nonces.json"

    attestation = generate_child_attestation(
        repo=tmp_path,
        plan_path=plan_path,
        command=str(fake_codex),
        extra_args=[],
        now=NOW,
        ttl_seconds=300,
    )
    verified = verify_child_attestation(
        attestation,
        repo=tmp_path,
        plan_path=plan_path,
        manifest_path=manifest_path,
        required_skills=REQUIRED_SKILLS,
        command=str(fake_codex),
        extra_args=[],
        nonce_ledger=ledger,
        now=NOW + timedelta(seconds=1),
    )

    assert verified.loaded_skills == REQUIRED_SKILLS
    assert verified.child_home == str(child_home.resolve())
    assert verified.plan_sha256 == hashlib.sha256(plan_path.read_bytes()).hexdigest()
    assert verified.codex_cli_version == "codex-cli 9.9.9"
    assert verified.discovery_command
    assert verified.runtime_tree_sha256
    assert verified.plugin_tree_sha256
    assert json.loads(ledger.read_text(encoding="utf-8")) == [verified.nonce]


def test_exact_manifest_rejects_undeclared_runtime_entry(tmp_path, monkeypatch):
    child_home, manifest_path, _ = write_child_runtime(tmp_path, monkeypatch)
    (child_home / "skills" / "undeclared.py").write_text("# hidden runtime input\n", encoding="utf-8")

    with pytest.raises(ChildAttestationError, match="skills closure mismatch"):
        load_child_closure_manifest(child_home, manifest_path)


def test_empty_plugin_manifest_ignores_codex_owned_cache_and_staging(tmp_path, monkeypatch):
    child_home, manifest_path, _ = write_child_runtime(
        tmp_path,
        monkeypatch,
        plugin_relative=None,
    )
    cache = child_home / "plugins" / "cache"
    cache.mkdir(parents=True)
    (cache / "remote-catalog.json").write_text("{}\n", encoding="utf-8")
    cached_payload = cache / "remote-marketplace" / "cached-plugin" / "9.9.9"
    cached_payload.mkdir(parents=True)
    (cached_payload / "plugin.json").write_text('{"name":"cached"}\n', encoding="utf-8")
    (child_home / "plugins" / ".remote-plugin-install-staging").mkdir()

    manifest = load_child_closure_manifest(child_home, manifest_path)

    assert manifest.plugin_tree_sha256


def test_cache_plugin_payload_is_hash_bound_and_rejects_undeclared_version(tmp_path, monkeypatch):
    plugin_relative = "plugins/cache/marketplace/example/1.0.0"
    child_home, manifest_path, _ = write_child_runtime(
        tmp_path,
        monkeypatch,
        plugin_relative=plugin_relative,
    )

    load_child_closure_manifest(child_home, manifest_path)

    extra = child_home / "plugins" / "cache" / "marketplace" / "example" / "2.0.0"
    extra.mkdir(parents=True)
    (extra / "plugin.json").write_text('{"name":"extra"}\n', encoding="utf-8")
    with pytest.raises(ChildAttestationError, match="plugins closure mismatch"):
        load_child_closure_manifest(child_home, manifest_path)


@pytest.mark.parametrize(
    ("path", "sha256", "message"),
    [
        ("plugins/codex-flow-runtime", "0" * 64, "digest mismatch"),
        ("../outside-plugin", None, "path escapes child home"),
    ],
)
def test_plugin_payload_rejects_wrong_hash_and_path_escape(
    tmp_path, monkeypatch, path, sha256, message
):
    child_home, manifest_path, _ = write_child_runtime(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if sha256 is None:
        outside = child_home.parent / "outside-plugin"
        outside.mkdir()
        (outside / "plugin.json").write_text("{}\n", encoding="utf-8")
        sha256 = path_tree_hash(outside)
    manifest["plugins"][0]["path"] = path
    manifest["plugins"][0]["sha256"] = sha256
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    with pytest.raises(ChildAttestationError, match=message):
        load_child_closure_manifest(child_home, manifest_path)


def test_plugin_and_external_skill_ids_form_exact_discovered_closure(tmp_path, monkeypatch):
    external_ids = ("superpowers:using-superpowers", "superpowers:test-driven-development")
    plugin_ids = ("plugin:example-skill",)
    discovered = (*REQUIRED_SKILLS, *plugin_ids, *external_ids)
    child_home, manifest_path, fake_codex = write_child_runtime(
        tmp_path,
        monkeypatch,
        discovered_ids=discovered,
        plugin_skill_ids=plugin_ids,
        external_skill_ids=external_ids,
    )
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# approved plan\n", encoding="utf-8")

    attestation = generate_child_attestation(
        repo=tmp_path,
        plan_path=plan_path,
        command=str(fake_codex),
        extra_args=[],
        now=NOW,
    )
    verified = verify_child_attestation(
        attestation,
        repo=tmp_path,
        plan_path=plan_path,
        manifest_path=manifest_path,
        required_skills=REQUIRED_SKILLS,
        command=str(fake_codex),
        extra_args=[],
        nonce_ledger=tmp_path / "used-nonces.json",
        now=NOW,
    )

    assert verified.loaded_skills == discovered
    assert verified.external_skill_ids == external_ids
    assert verified.to_dict()["external_skill_ids"] == list(external_ids)


def test_short_required_skill_names_resolve_to_one_namespaced_plugin_skill(tmp_path, monkeypatch):
    plugin_ids = (
        "chronica-workflow:plan-first-implementation",
        "chronica-workflow:review-all-in-one",
    )
    _, manifest_path, fake_codex = write_child_runtime(
        tmp_path,
        monkeypatch,
        skill_ids=(),
        discovered_ids=plugin_ids,
        plugin_skill_ids=plugin_ids,
    )
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# approved plan\n", encoding="utf-8")
    attestation = generate_child_attestation(
        repo=tmp_path,
        plan_path=plan_path,
        command=str(fake_codex),
        extra_args=[],
        now=NOW,
    )

    verified = verify_child_attestation(
        attestation,
        repo=tmp_path,
        plan_path=plan_path,
        manifest_path=manifest_path,
        required_skills=REQUIRED_SKILLS,
        command=str(fake_codex),
        extra_args=[],
        nonce_ledger=tmp_path / "used-nonces.json",
        now=NOW,
    )

    assert verified.loaded_skills == plugin_ids


@pytest.mark.parametrize(
    "discovered_ids",
    [
        (*REQUIRED_SKILLS, "superpowers:using-superpowers"),
        (
            *REQUIRED_SKILLS,
            "superpowers:using-superpowers",
            "superpowers:test-driven-development",
            "superpowers:unexpected",
        ),
    ],
)
def test_external_skill_allowlist_rejects_missing_or_extra_discovery(
    tmp_path, monkeypatch, discovered_ids
):
    external_ids = ("superpowers:using-superpowers", "superpowers:test-driven-development")
    _, manifest_path, fake_codex = write_child_runtime(
        tmp_path,
        monkeypatch,
        discovered_ids=discovered_ids,
        external_skill_ids=external_ids,
    )
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# approved plan\n", encoding="utf-8")
    attestation = generate_child_attestation(
        repo=tmp_path,
        plan_path=plan_path,
        command=str(fake_codex),
        extra_args=[],
        now=NOW,
    )

    with pytest.raises(ChildAttestationError, match="discovered skill closure mismatch"):
        verify_child_attestation(
            attestation,
            repo=tmp_path,
            plan_path=plan_path,
            manifest_path=manifest_path,
            required_skills=REQUIRED_SKILLS,
            command=str(fake_codex),
            extra_args=[],
            nonce_ledger=tmp_path / "used-nonces.json",
            now=NOW,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("child_home", "/tmp/wrong-child", "child path"),
        ("plan_sha256", "0" * 64, "plan hash"),
        ("runtime_tree_sha256", "1" * 64, "runtime tree hash"),
        ("plugin_tree_sha256", "2" * 64, "plugin tree hash"),
        ("external_skill_ids", ("superpowers:forged",), "external skill ids"),
        ("codex_cli_version", "codex-cli 0.0.0", "Codex CLI version"),
        ("discovery_command", ("forged",), "discovery command"),
    ],
)
def test_attestation_rejects_mismatched_bound_inputs(tmp_path, monkeypatch, field, value, message):
    init_git_repo(tmp_path)
    _, manifest_path, fake_codex = write_child_runtime(tmp_path, monkeypatch)
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# approved plan\n", encoding="utf-8")
    attestation = generate_child_attestation(
        repo=tmp_path,
        plan_path=plan_path,
        command=str(fake_codex),
        extra_args=[],
        now=NOW,
    )
    before = git_oracle(tmp_path)

    with pytest.raises(ChildAttestationError, match=message):
        verify_child_attestation(
            resign(attestation, **{field: value}),
            repo=tmp_path,
            plan_path=plan_path,
            manifest_path=manifest_path,
            required_skills=REQUIRED_SKILLS,
            command=str(fake_codex),
            extra_args=[],
            nonce_ledger=tmp_path / "used-nonces.json",
            now=NOW,
        )
    assert git_oracle(tmp_path) == before


def test_attestation_rejects_missing_required_skill_staleness_and_replay(tmp_path, monkeypatch):
    init_git_repo(tmp_path)
    _, manifest_path, fake_codex = write_child_runtime(
        tmp_path,
        monkeypatch,
        discovered_ids=("plan-first-implementation",),
    )
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# approved plan\n", encoding="utf-8")
    attestation = generate_child_attestation(
        repo=tmp_path,
        plan_path=plan_path,
        command=str(fake_codex),
        extra_args=[],
        now=NOW,
        ttl_seconds=30,
    )
    common = {
        "repo": tmp_path,
        "plan_path": plan_path,
        "manifest_path": manifest_path,
        "required_skills": REQUIRED_SKILLS,
        "command": str(fake_codex),
        "extra_args": [],
        "nonce_ledger": tmp_path / ".codex-flow" / "attestations" / "used-nonces.json",
    }
    before = git_oracle(tmp_path)

    with pytest.raises(ChildAttestationError, match="missing required skills"):
        verify_child_attestation(attestation, now=NOW, **common)
    assert git_oracle(tmp_path) == before

    complete = resign(attestation, loaded_skills=REQUIRED_SKILLS)
    with pytest.raises(ChildAttestationError, match="stale"):
        verify_child_attestation(complete, now=NOW + timedelta(seconds=31), **common)
    assert git_oracle(tmp_path) == before

    verify_child_attestation(complete, now=NOW + timedelta(seconds=1), **common)
    with pytest.raises(ChildAttestationError, match="replayed nonce"):
        verify_child_attestation(complete, now=NOW + timedelta(seconds=1), **common)
    assert git_oracle(tmp_path) == before


def test_nonce_claim_rejects_concurrent_replay(tmp_path):
    ledger = tmp_path / "used-nonces.json"

    def claim():
        try:
            consume_attestation_nonce(ledger, "same-nonce")
            return "accepted"
        except ChildAttestationError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: claim(), range(2)))

    assert sorted(outcomes) == ["accepted", "rejected"]


def init_git_repo(repo: Path):
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "switch", "-c", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "codex-flow@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Codex Flow"], cwd=repo, check=True)
    (repo / "README.md").write_text("# Test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)


def git_oracle(repo: Path):
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout
    index = subprocess.run(["git", "write-tree"], cwd=repo, check=True, capture_output=True, text=True).stdout
    worktree = hashlib.sha256()
    for path in sorted(repo.rglob("*")):
        relative = path.relative_to(repo)
        if relative.parts[0] in {".git", ".codex-flow"} or not path.is_file():
            continue
        worktree.update(relative.as_posix().encode("utf-8") + b"\0" + path.read_bytes())
    return head, index, worktree.hexdigest()


def test_runner_missing_attestation_inputs_never_launches_implementer_or_edits_repo(tmp_path, monkeypatch):
    init_git_repo(tmp_path)
    ticket = tickets.submit_ticket("fail closed", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)
    marker = tmp_path.parent / f"launched-{tmp_path.name}"
    fake_codex = tmp_path.parent / f"must-not-launch-{tmp_path.name}.py"
    fake_codex.write_text(
        f"#!/usr/bin/env python3\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('launched')\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    monkeypatch.delenv("CODEX_FLOW_CHILD_HOME", raising=False)
    monkeypatch.delenv(CHILD_MANIFEST_ENV, raising=False)
    before = git_oracle(tmp_path)

    result = runner.run_next(
        plan.plan_path,
        execute=True,
        commit=False,
        codex_command=str(fake_codex),
    )

    assert result["action"] == "needs_work"
    assert "prepared child home" in result["reason"]
    assert not marker.exists()
    assert git_oracle(tmp_path) == before


def test_runner_valid_exact_attestation_allows_fixture_execution(tmp_path, monkeypatch):
    init_git_repo(tmp_path)
    all_skills = (
        "요청개선",
        "plan-first-implementation",
        "mission-completion-harness",
        "review-all-in-one",
    )
    _, _, fake_codex = write_child_runtime(
        tmp_path,
        monkeypatch,
        skill_ids=all_skills,
        full_agent=True,
    )
    ticket = tickets.submit_ticket("attested execution", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)
    queue = json.loads(plan.queue_json.read_text(encoding="utf-8"))
    queue["units"][0]["allowed_paths"].append("work.txt")
    plan.queue_json.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = runner.run_next(
        plan.plan_path,
        execute=True,
        commit=False,
        codex_command=str(fake_codex),
    )

    assert result["action"] == "done"
    assert (tmp_path / "work.txt").read_text(encoding="utf-8") == "implemented\n"
    attestation_path = plan.directory / result["unit"]["child_attestation"]
    evidence = json.loads(attestation_path.read_text(encoding="utf-8"))
    assert evidence["loaded_skills"] == list(all_skills)


def test_runner_missing_discovered_skill_never_launches_implementer_or_edits_repo(tmp_path, monkeypatch):
    init_git_repo(tmp_path)
    all_skills = (
        "요청개선",
        "plan-first-implementation",
        "mission-completion-harness",
        "review-all-in-one",
    )
    _, _, fake_codex = write_child_runtime(
        tmp_path,
        monkeypatch,
        skill_ids=all_skills,
        discovered_ids=("plan-first-implementation", "mission-completion-harness", "review-all-in-one"),
        full_agent=True,
    )
    ticket = tickets.submit_ticket("missing discovery skill", repo=tmp_path)
    plan = plans.create_plan_from_ticket(ticket.path, repo=tmp_path)
    before = git_oracle(tmp_path)

    result = runner.run_next(
        plan.plan_path,
        execute=True,
        commit=False,
        codex_command=str(fake_codex),
    )

    assert result["action"] == "needs_work"
    assert "missing required skills before edit" in result["reason"]
    assert not (tmp_path / "work.txt").exists()
    assert git_oracle(tmp_path) == before
