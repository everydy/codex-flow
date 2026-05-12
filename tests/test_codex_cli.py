from __future__ import annotations

from pathlib import Path

from codex_flow.codex_cli import codex_cli_default_args, parse_session_id, run_codex_exec


def test_codex_cli_default_args_include_model_and_fast_mode():
    args = codex_cli_default_args()

    assert "gpt-5.5" in args
    assert 'model_reasoning_effort="xhigh"' in args
    assert 'service_tier="fast"' in args
    assert "features.fast_mode=true" in args


def test_run_codex_exec_reads_output_last_message(tmp_path):
    fake = write_fake_codex(tmp_path, "FINAL_LINE\n")

    result = run_codex_exec("hello", repo=tmp_path, command=str(fake), extra_args=[])

    assert result.status == 0
    assert result.final_message == "FINAL_LINE\n"
    assert result.output_path.exists()


def test_parse_session_id_from_jsonl_and_uuid_fallback():
    assert parse_session_id('{"session_id":"abc"}\n') == "abc"
    assert parse_session_id('noise 11111111-2222-3333-4444-555555555555') == "11111111-2222-3333-4444-555555555555"


def write_fake_codex(tmp_path: Path, final_message: str) -> Path:
    fake = tmp_path / "fake_codex.py"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "out = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
        f"out.write_text({final_message!r}, encoding='utf-8')\n"
        "print('{\"session_id\":\"fake-session\"}')\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | 0o111)
    return fake
