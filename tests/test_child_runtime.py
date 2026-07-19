from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from codex_flow.child_runtime import PreparedChildRuntime, ensure_prepared_child_runtime
from codex_flow.codex_cli import (
    CHILD_HOME_ENV,
    CHILD_MANIFEST_ENV,
    ChildRuntimeConfigError,
    load_child_closure_manifest,
)


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "config.toml").write_text('model = "test-model"\n', encoding="utf-8")
    (parent / "auth.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent))
    monkeypatch.setenv("CODEX_CHILD_RUNTIME_ROOT", str(tmp_path / "runtimes"))
    monkeypatch.delenv(CHILD_HOME_ENV, raising=False)
    monkeypatch.delenv(CHILD_MANIFEST_ENV, raising=False)


def make_skill(parent: Path, skill_id: str, content: str = "instructions") -> Path:
    skill = parent / "skills" / skill_id
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(content, encoding="utf-8")
    return skill


def write_inventory_codex(tmp_path: Path) -> Path:
    script = tmp_path / "fake-codex.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

if sys.argv[1:] == ["--version"]:
    print(os.environ.get("FAKE_CODEX_VERSION", "codex-cli test-1"))
    raise SystemExit(0)
if sys.argv[1:] != ["app-server", "--listen", "stdio://"]:
    raise SystemExit(2)
home = Path(os.environ["CODEX_HOME"])
for line in sys.stdin:
    request = json.loads(line)
    if request["method"] == "initialize":
        result = {"codexHome": str(home)}
    else:
        cwd = request["params"]["cwds"][0]
        skills = [
            {"name": path.name, "path": str(path / "SKILL.md"), "enabled": True}
            for path in sorted((home / "skills").iterdir())
        ] if (home / "skills").exists() else []
        plugin_id = os.environ.get("FAKE_PLUGIN_SKILL")
        if plugin_id:
            plugin = home / "plugins" / "cache" / "market" / "example" / "1.0.0"
            plugin.mkdir(parents=True, exist_ok=True)
            plugin_skill = plugin / "skills" / plugin_id.replace(":", "-")
            plugin_skill.mkdir(parents=True, exist_ok=True)
            (plugin_skill / "SKILL.md").write_text("plugin", encoding="utf-8")
            skills.append({"name": plugin_id, "path": str(plugin_skill / "SKILL.md"), "enabled": True})
        external_id = os.environ.get("FAKE_EXTERNAL_SKILL")
        external_path = os.environ.get("FAKE_EXTERNAL_PATH")
        if external_id and external_path:
            skills.append({"name": external_id, "path": external_path, "enabled": True})
        result = {"data": [{"cwd": cwd, "errors": [], "skills": skills}]}
    print(json.dumps({"id": request["id"], "result": result}), flush=True)
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def prepare(tmp_path: Path, *, required=("alpha",)) -> PreparedChildRuntime:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    parent = Path(__import__("os").environ["CODEX_HOME"])
    for skill_id in required:
        if ":" not in skill_id and not (parent / "skills" / skill_id).exists():
            make_skill(parent, skill_id)
    return ensure_prepared_child_runtime(
        repo=repo,
        required_skills=required,
        command=str(write_inventory_codex(tmp_path)),
    )


def test_managed_runtime_is_private_exact_and_reused(tmp_path):
    first = prepare(tmp_path)
    second = prepare(tmp_path)

    assert first.source == "managed"
    assert first.reused is False
    assert second == PreparedChildRuntime(
        home=first.home,
        manifest=first.manifest,
        source="managed",
        cache_key=first.cache_key,
        reused=True,
    )
    assert first.home.stat().st_mode & 0o077 == 0
    assert first.manifest.stat().st_mode & 0o077 == 0
    assert (first.home / "auth.json").is_symlink()
    assert (first.home / "skills" / "alpha" / "SKILL.md").read_text() == "instructions"
    manifest = load_child_closure_manifest(first.home, first.manifest)
    assert manifest.skill_ids == ("alpha",)


def test_source_change_invalidates_cache_key(tmp_path):
    first = prepare(tmp_path)
    source = Path(__import__("os").environ["CODEX_HOME"]) / "skills" / "alpha" / "SKILL.md"
    source.write_text("changed", encoding="utf-8")

    second = prepare(tmp_path)

    assert second.cache_key != first.cache_key
    assert second.home != first.home


def test_config_and_cli_version_changes_invalidate_cache_key(tmp_path, monkeypatch):
    first = prepare(tmp_path)
    parent = Path(__import__("os").environ["CODEX_HOME"])
    (parent / "config.toml").write_text('model = "other-model"\n', encoding="utf-8")
    second = prepare(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_VERSION", "codex-cli test-2")
    third = prepare(tmp_path)

    assert len({first.cache_key, second.cache_key, third.cache_key}) == 3


def test_plugin_payload_and_external_skill_are_classified_exactly(tmp_path, monkeypatch):
    external = tmp_path / "external" / "SKILL.md"
    external.parent.mkdir()
    external.write_text("external", encoding="utf-8")
    monkeypatch.setenv("FAKE_PLUGIN_SKILL", "plugin:review")
    monkeypatch.setenv("FAKE_EXTERNAL_SKILL", "external:tool")
    monkeypatch.setenv("FAKE_EXTERNAL_PATH", str(external))

    runtime = prepare(tmp_path, required=("plugin:review", "external:tool"))
    manifest = load_child_closure_manifest(runtime.home, runtime.manifest)

    assert manifest.skill_ids == ()
    assert manifest.plugin_skill_ids == ("plugin:review",)
    assert manifest.external_skill_ids == ("external:tool",)
    data = json.loads(runtime.manifest.read_text(encoding="utf-8"))
    assert data["plugins"][0]["path"] == "plugins/cache/market/example/1.0.0"


def test_explicit_pair_is_validated_without_mutation(tmp_path, monkeypatch):
    managed = prepare(tmp_path)
    before = managed.manifest.read_bytes()
    monkeypatch.setenv(CHILD_HOME_ENV, str(managed.home))
    monkeypatch.setenv(CHILD_MANIFEST_ENV, str(managed.manifest))

    explicit = ensure_prepared_child_runtime(
        repo=tmp_path / "repo",
        required_skills=("alpha",),
        command=str(write_inventory_codex(tmp_path)),
    )

    assert explicit.source == "explicit"
    assert explicit.reused is True
    assert explicit.manifest.read_bytes() == before


def test_explicit_pair_rejects_parent_codex_home(tmp_path, monkeypatch):
    managed = prepare(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(managed.home))
    monkeypatch.setenv(CHILD_HOME_ENV, str(managed.home))
    monkeypatch.setenv(CHILD_MANIFEST_ENV, str(managed.manifest))

    with pytest.raises(ChildRuntimeConfigError, match="parent CODEX_HOME"):
        ensure_prepared_child_runtime(
            repo=tmp_path / "repo",
            required_skills=("alpha",),
        )


def test_half_explicit_pair_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv(CHILD_HOME_ENV, str(tmp_path / "home"))

    with pytest.raises(ChildRuntimeConfigError, match="both"):
        ensure_prepared_child_runtime(repo=tmp_path, required_skills=("alpha",))


def test_managed_runtime_rejects_explicit_profile_without_override(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    parent = Path(__import__("os").environ["CODEX_HOME"])
    make_skill(parent, "alpha")

    with pytest.raises(ChildRuntimeConfigError, match="profile"):
        ensure_prepared_child_runtime(
            repo=repo,
            required_skills=("alpha",),
            extra_args=["--profile", "special"],
        )


def test_missing_and_unsafe_skill_sources_are_rejected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ChildRuntimeConfigError, match="missing required skill source"):
        ensure_prepared_child_runtime(repo=repo, required_skills=("missing",))

    parent = Path(__import__("os").environ["CODEX_HOME"])
    source = make_skill(parent, "linked")
    (source / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(ChildRuntimeConfigError, match="symlink"):
        ensure_prepared_child_runtime(repo=repo, required_skills=("linked",))


def test_repo_local_source_is_rejected(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    parent = Path(__import__("os").environ["CODEX_HOME"])
    (parent / "skills").mkdir()
    (parent / "skills" / "local").symlink_to(make_skill(repo, "local"))

    with pytest.raises(ChildRuntimeConfigError, match="target repository"):
        ensure_prepared_child_runtime(repo=repo, required_skills=("local",))


def test_concurrent_callers_receive_one_validated_winner(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    parent = Path(__import__("os").environ["CODEX_HOME"])
    make_skill(parent, "alpha")
    command = str(write_inventory_codex(tmp_path))

    def call():
        return ensure_prepared_child_runtime(
            repo=repo, required_skills=("alpha",), command=command
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: call(), range(2)))

    assert results[0].home == results[1].home
    assert sorted(result.reused for result in results) == [False, True]
    for result in results:
        load_child_closure_manifest(result.home, result.manifest)
