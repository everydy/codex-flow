from __future__ import annotations

from pathlib import Path

import pytest

from codex_flow.codex_cli import CodexExecFailure, CodexExecTimeout, codex_cli_default_args, parse_session_id, run_codex_exec, with_codex_cli_defaults


def test_codex_cli_default_args_use_supported_environment_model_and_fast_mode(monkeypatch):
    monkeypatch.setenv("CODEX_FLOW_MODEL", "gpt-5.4")
    args = codex_cli_default_args()

    assert args[:2] == ["--model", "gpt-5.4"]
    assert 'model_reasoning_effort="xhigh"' in args
    assert 'service_tier="fast"' in args
    assert "features.fast_mode=true" in args


def test_codex_cli_defaults_to_verified_model_when_environment_is_unset(monkeypatch):
    monkeypatch.delenv("CODEX_FLOW_MODEL", raising=False)

    args = codex_cli_default_args()

    assert args[:2] == ["--model", "gpt-5.5"]


def test_unsupported_environment_model_falls_back_to_verified_model(monkeypatch):
    monkeypatch.setenv("CODEX_FLOW_MODEL", "gpt-5.6-sol")

    args = codex_cli_default_args()

    assert args[:2] == ["--model", "gpt-5.5"]
    assert "gpt-5.6-sol" not in args


def test_supported_explicit_codex_model_arg_overrides_environment_model(monkeypatch):
    monkeypatch.setenv("CODEX_FLOW_MODEL", "gpt-5.4")

    args = with_codex_cli_defaults(["--model", "gpt-5.4-mini"])

    assert args[-2:] == ["--model", "gpt-5.4-mini"]


def test_unsupported_explicit_codex_model_arg_is_replaced_with_verified_fallback(monkeypatch):
    monkeypatch.setenv("CODEX_FLOW_MODEL", "gpt-5.4")

    args = with_codex_cli_defaults(["--model", "gpt-5.6-sol"])

    assert args[-2:] == ["--model", "gpt-5.5"]
    assert "gpt-5.6-sol" not in args


def test_supported_model_allowlist_can_be_extended_explicitly(monkeypatch):
    monkeypatch.setenv("CODEX_FLOW_MODEL", "future-model")
    monkeypatch.setenv("CODEX_FLOW_SUPPORTED_MODELS", "gpt-5.5,future-model")

    args = codex_cli_default_args()

    assert args[:2] == ["--model", "future-model"]


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


def test_run_codex_exec_writes_diagnostics(tmp_path):
    fake = write_fake_codex(tmp_path, "FINAL_LINE\n")
    diagnostic_dir = tmp_path / "diagnostics"

    result = run_codex_exec("hello", repo=tmp_path, command=str(fake), extra_args=[], diagnostic_dir=diagnostic_dir, phase="implementation")

    assert result.status == 0
    assert result.output_path == diagnostic_dir / "last-message.txt"
    assert (diagnostic_dir / "prompt.md").read_text(encoding="utf-8") == "hello"
    assert "fake-session" in (diagnostic_dir / "stdout.log").read_text(encoding="utf-8")
    assert '"phase": "implementation"' in (diagnostic_dir / "metadata.json").read_text(encoding="utf-8")


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
