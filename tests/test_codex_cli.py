from __future__ import annotations

from pathlib import Path

import pytest

from codex_flow.codex_cli import (
    ChildRuntimeConfigError,
    CodexExecFailure,
    CodexExecTimeout,
    child_runtime_environment,
    codex_cli_default_args,
    parse_session_id,
    run_codex_exec,
    with_codex_cli_defaults,
)


@pytest.fixture(autouse=True)
def isolated_child_runtime_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_CHILD_RUNTIME_ROOT", str(tmp_path.parent / f"{tmp_path.name}-child-runtimes"))


def test_codex_cli_default_args_use_environment_model_without_forcing_runtime_tuning(monkeypatch):
    monkeypatch.setenv("CODEX_FLOW_MODEL", "gpt-5.6-sol")
    args = codex_cli_default_args()

    assert args == ["--model", "gpt-5.6-sol"]


def test_codex_cli_inherits_codex_config_when_environment_is_unset(monkeypatch):
    monkeypatch.delenv("CODEX_FLOW_MODEL", raising=False)

    args = codex_cli_default_args()

    assert args == []


def test_explicit_codex_model_arg_overrides_environment_model_without_rewriting(monkeypatch):
    monkeypatch.setenv("CODEX_FLOW_MODEL", "gpt-5.4")

    args = with_codex_cli_defaults(["--model", "operator-selected-model"])

    assert args == ["--model", "operator-selected-model"]


def test_short_explicit_model_arg_is_preserved(monkeypatch):
    monkeypatch.setenv("CODEX_FLOW_MODEL", "gpt-5.4")

    args = with_codex_cli_defaults(["-m", "gpt-5.6-terra", "--ephemeral"])

    assert args == ["-m", "gpt-5.6-terra", "--ephemeral"]


def test_run_codex_exec_reads_output_last_message(tmp_path):
    fake = write_fake_codex(tmp_path, "FINAL_LINE\n")

    result = run_codex_exec("hello", repo=tmp_path, command=str(fake), extra_args=[])

    assert result.status == 0
    assert result.final_message == "FINAL_LINE\n"
    assert result.output_path.exists()


def test_run_codex_exec_disables_closeout_hooks_for_machine_readable_agents(tmp_path):
    fake = write_fake_codex(
        tmp_path,
        "FINAL_LINE\n",
        assert_env={"CODEX_CLOSEOUT_HOOK_DISABLED": "1"},
    )

    result = run_codex_exec("hello", repo=tmp_path, command=str(fake), extra_args=[])

    assert result.status == 0
    assert result.final_message == "FINAL_LINE\n"


def test_child_runtime_environment_isolates_safe_config_outside_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    parent_home = tmp_path / "parent-codex"
    runtime_root = tmp_path / "child-runtimes"
    repo.mkdir()
    parent_home.mkdir()
    (parent_home / "auth.json").write_text("{}\n", encoding="utf-8")
    (parent_home / "config.toml").write_text(
        '\n'.join(
            [
                'model = "gpt-current"',
                'model_reasoning_effort = "medium"',
                'service_tier = "default"',
                '',
                '[mcp_servers.noisy]',
                'url = "https://example.invalid"',
                '',
                f'[projects."{repo}"]',
                'trust_level = "trusted"',
                '',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setenv("CODEX_CHILD_RUNTIME_ROOT", str(runtime_root))

    env, metadata = child_runtime_environment(repo)

    child_home = Path(env["CODEX_HOME"])
    assert child_home.is_relative_to(runtime_root)
    assert not child_home.is_relative_to(repo)
    assert runtime_root.stat().st_mode & 0o077 == 0
    assert child_home.parent.stat().st_mode & 0o077 == 0
    assert child_home.stat().st_mode & 0o077 == 0
    assert (child_home / "auth.json").is_symlink()
    child_config = (child_home / "config.toml").read_text(encoding="utf-8")
    assert 'model = "gpt-current"' in child_config
    assert 'model_reasoning_effort = "medium"' in child_config
    assert 'service_tier = "default"' in child_config
    assert "mcp_servers" not in child_config
    assert f'[projects."{repo.resolve()}"]' in child_config
    assert 'trust_level = "trusted"' in child_config
    assert "enabled = false" in child_config
    assert env["CODEX_BOOTSTRAP_HOOK_DISABLED"] == "1"
    assert env["CODEX_REQUEST_REFINER_HOOK_DISABLED"] == "1"
    assert env["CODEX_CLOSEOUT_HOOK_DISABLED"] == "1"
    assert metadata["mode"] == "isolated"


def test_child_runtime_environment_reuses_repo_namespace(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    parent_home = tmp_path / "parent-codex"
    repo.mkdir()
    parent_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setenv("CODEX_CHILD_RUNTIME_ROOT", str(tmp_path / "child-runtimes"))

    first, _ = child_runtime_environment(repo)
    second, _ = child_runtime_environment(repo)

    assert first["CODEX_HOME"] == second["CODEX_HOME"]


def test_child_runtime_environment_opt_out_preserves_parent_home(tmp_path, monkeypatch):
    parent_home = tmp_path / "parent-codex"
    parent_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setenv("CODEX_FLOW_CHILD_ISOLATION", "0")

    env, metadata = child_runtime_environment(tmp_path)

    assert env["CODEX_HOME"] == str(parent_home)
    assert metadata["mode"] == "disabled"


def test_child_runtime_environment_rejects_repo_local_override(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_FLOW_CHILD_HOME", str(repo / ".child-codex"))

    with pytest.raises(ChildRuntimeConfigError, match="outside the target repository"):
        child_runtime_environment(repo)


def test_child_runtime_environment_rejects_parent_home_as_override(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    parent_home = tmp_path / "parent-codex"
    repo.mkdir()
    parent_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setenv("CODEX_FLOW_CHILD_HOME", str(parent_home))

    with pytest.raises(ChildRuntimeConfigError, match="must differ from the parent"):
        child_runtime_environment(repo)


def test_run_codex_exec_rejects_profile_without_explicit_child_home(tmp_path, monkeypatch):
    parent_home = tmp_path / "parent-codex"
    parent_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setenv("CODEX_CHILD_RUNTIME_ROOT", str(tmp_path / "child-runtimes"))

    with pytest.raises(ChildRuntimeConfigError, match="profile"):
        run_codex_exec(
            "hello",
            repo=tmp_path,
            command=str(write_fake_codex(tmp_path, "FINAL_LINE\n")),
            extra_args=["--profile", "special"],
        )


def test_child_runtime_environment_rejects_unsanitized_custom_provider(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    parent_home = tmp_path / "parent-codex"
    repo.mkdir()
    parent_home.mkdir()
    (parent_home / "config.toml").write_text('model_provider = "custom"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setenv("CODEX_CHILD_RUNTIME_ROOT", str(tmp_path / "child-runtimes"))

    with pytest.raises(ChildRuntimeConfigError, match="custom model provider"):
        child_runtime_environment(repo)


def test_run_codex_exec_writes_diagnostics(tmp_path):
    fake = write_fake_codex(tmp_path, "FINAL_LINE\n")
    diagnostic_dir = tmp_path / "diagnostics"

    result = run_codex_exec("hello", repo=tmp_path, command=str(fake), extra_args=[], diagnostic_dir=diagnostic_dir, phase="implementation")

    assert result.status == 0
    assert result.output_path == diagnostic_dir / "last-message.txt"
    assert (diagnostic_dir / "prompt.md").read_text(encoding="utf-8") == "hello"
    assert "fake-session" in (diagnostic_dir / "stdout.log").read_text(encoding="utf-8")
    assert '"phase": "implementation"' in (diagnostic_dir / "metadata.json").read_text(encoding="utf-8")
    assert '"source": "inherited"' in (diagnostic_dir / "metadata.json").read_text(encoding="utf-8")


def test_run_codex_exec_records_explicit_model_source(tmp_path):
    fake = write_fake_codex(tmp_path, "FINAL_LINE\n")
    diagnostic_dir = tmp_path / "diagnostics"

    run_codex_exec(
        "hello",
        repo=tmp_path,
        command=str(fake),
        extra_args=["--model", "operator-selected-model"],
        diagnostic_dir=diagnostic_dir,
    )

    metadata = (diagnostic_dir / "metadata.json").read_text(encoding="utf-8")
    assert '"source": "explicit"' in metadata
    assert '"requested": "operator-selected-model"' in metadata


def test_run_codex_exec_timeout_preserves_diagnostics(tmp_path):
    fake = write_sleeping_codex(tmp_path)
    diagnostic_dir = tmp_path / "timeout-diagnostics"

    with pytest.raises(CodexExecTimeout) as exc:
        run_codex_exec("hello", repo=tmp_path, command=str(fake), extra_args=[], timeout_seconds=1, diagnostic_dir=diagnostic_dir)

    assert exc.value.diagnostic_dir == diagnostic_dir.resolve()
    assert (diagnostic_dir / "prompt.md").exists()
    assert '"status": "timeout"' in (diagnostic_dir / "metadata.json").read_text(encoding="utf-8")


def test_run_codex_exec_failure_preserves_diagnostics(tmp_path):
    fake = write_failing_codex(tmp_path)
    diagnostic_dir = tmp_path / "failure-diagnostics"

    with pytest.raises(CodexExecFailure) as exc:
        run_codex_exec("hello", repo=tmp_path, command=str(fake), extra_args=[], diagnostic_dir=diagnostic_dir)

    assert exc.value.status == 7
    assert exc.value.diagnostic_dir == diagnostic_dir.resolve()
    assert "bad things" in (diagnostic_dir / "stderr.log").read_text(encoding="utf-8")
    assert '"status": "failed"' in (diagnostic_dir / "metadata.json").read_text(encoding="utf-8")


def test_parse_session_id_from_jsonl_and_uuid_fallback():
    assert parse_session_id('{"session_id":"abc"}\n') == "abc"
    assert parse_session_id('noise 11111111-2222-3333-4444-555555555555') == "11111111-2222-3333-4444-555555555555"


def write_fake_codex(tmp_path: Path, final_message: str, *, assert_env: dict[str, str] | None = None) -> Path:
    env_checks = ""
    for key, value in (assert_env or {}).items():
        env_checks += f"assert os.environ.get({key!r}) == {value!r}, os.environ.get({key!r})\n"
    fake = tmp_path / "fake_codex.py"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import pathlib, sys\n"
        f"{env_checks}"
        "args = sys.argv[1:]\n"
        "out = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
        f"out.write_text({final_message!r}, encoding='utf-8')\n"
        "print('{\"session_id\":\"fake-session\"}')\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | 0o111)
    return fake


def write_sleeping_codex(tmp_path: Path) -> Path:
    fake = tmp_path / "sleeping_codex.py"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import time\n"
        "time.sleep(5)\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | 0o111)
    return fake


def write_failing_codex(tmp_path: Path) -> Path:
    fake = tmp_path / "failing_codex.py"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('bad things', file=sys.stderr)\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | 0o111)
    return fake
