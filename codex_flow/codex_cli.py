from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import re
import subprocess
import tempfile

from .git_ops import ProcessResult, command_failure, run_process


CODEX_CLI_MODEL = "gpt-5.5"
CODEX_CLI_REASONING_EFFORT = "xhigh"
CODEX_CLI_SERVICE_TIER = "fast"
MACHINE_READABLE_AGENT_ENV = {"CODEX_CLOSEOUT_HOOK_DISABLED": "1"}


@dataclass(frozen=True)
class CodexExecResult:
    status: int
    stdout: str
    stderr: str
    final_message: str
    output_path: Path


class CodexExecTimeout(RuntimeError):
    def __init__(self, elapsed_seconds: int, diagnostic_dir: Path) -> None:
        super().__init__(f"codex exec timed out after {elapsed_seconds}s; diagnostics: {diagnostic_dir}")
        self.elapsed_seconds = elapsed_seconds
        self.diagnostic_dir = diagnostic_dir


class CodexExecFailure(RuntimeError):
    def __init__(self, status: int, diagnostic_dir: Path) -> None:
        super().__init__(f"codex exec failed with status {status}; diagnostics: {diagnostic_dir}")
        self.status = status
        self.diagnostic_dir = diagnostic_dir


def codex_cli_default_args() -> list[str]:
    return [
        "--model",
        CODEX_CLI_MODEL,
        "--config",
        f'model_reasoning_effort="{CODEX_CLI_REASONING_EFFORT}"',
        "--config",
        f'service_tier="{CODEX_CLI_SERVICE_TIER}"',
        "--config",
        "features.fast_mode=true",
    ]


def with_codex_cli_defaults(extra_args: list[str] | None = None) -> list[str]:
    return [*(extra_args or []), *codex_cli_default_args()]


def run_codex_exec(
    prompt: str,
    repo: str | Path,
    command: str = "codex",
    sandbox: str = "workspace-write",
    extra_args: list[str] | None = None,
    resume_session_id: str | None = None,
    timeout_seconds: int | None = None,
    diagnostic_dir: str | Path | None = None,
    phase: str = "codex-exec",
) -> CodexExecResult:
    repo_path = Path(repo).expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="codex-flow-agent-") as temp_dir:
        diag_dir = Path(diagnostic_dir).expanduser().resolve() if diagnostic_dir else None
        output_path = (diag_dir if diag_dir else Path(temp_dir)) / "last-message.txt"
        if resume_session_id:
            args = [
                command,
                "exec",
                "resume",
                "--json",
                "--output-last-message",
                str(output_path),
                *with_codex_cli_defaults(extra_args),
                resume_session_id,
                "-",
            ]
        else:
            args = [
                command,
                "exec",
                "--json",
                "--cd",
                str(repo_path),
                "--sandbox",
                sandbox,
                "--output-last-message",
                str(output_path),
                *with_codex_cli_defaults(extra_args),
                "-",
            ]
        if diag_dir:
            diag_dir.mkdir(parents=True, exist_ok=True)
            write_text(diag_dir / "prompt.md", prompt)
            write_json(diag_dir / "args.json", args)
            write_metadata(
                diag_dir,
                {
                    "phase": phase,
                    "status": "started",
                    "started_at": utc_now(),
                    "timeout_seconds": timeout_seconds,
                },
            )
        started = datetime.now(timezone.utc)
        result: ProcessResult | None = None
        try:
            result = run_process(
                args,
                cwd=repo_path,
                input_text=prompt,
                env={**os.environ, **MACHINE_READABLE_AGENT_ENV},
                timeout_seconds=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            if diag_dir:
                write_text(diag_dir / "stdout.log", output_to_text(exc.stdout))
                write_text(diag_dir / "stderr.log", output_to_text(exc.stderr))
                write_metadata(
                    diag_dir,
                    {
                        "phase": phase,
                        "status": "timeout",
                        "started_at": started.isoformat(),
                        "finished_at": utc_now(),
                        "timeout_seconds": timeout_seconds,
                        "elapsed_seconds": elapsed_seconds(started),
                    },
                )
                raise CodexExecTimeout(timeout_seconds or elapsed_seconds(started), diag_dir) from exc
            raise
        if diag_dir:
            write_text(diag_dir / "stdout.log", result.stdout)
            write_text(diag_dir / "stderr.log", result.stderr)
        final_message = output_path.read_text(encoding="utf-8") if output_path.exists() else result.stdout
        if diag_dir:
            stable_output_path = output_path
            write_text(stable_output_path, final_message)
            write_metadata(
                diag_dir,
                {
                    "phase": phase,
                    "status": "failed" if result.status != 0 else "pass",
                    "started_at": started.isoformat(),
                    "finished_at": utc_now(),
                    "timeout_seconds": timeout_seconds,
                    "elapsed_seconds": elapsed_seconds(started),
                },
            )
        else:
            with tempfile.NamedTemporaryFile(prefix="codex-flow-last-message-", suffix=".txt", delete=False) as stable_file:
                stable_output_path = Path(stable_file.name)
            stable_output_path.write_text(final_message, encoding="utf-8")

    if result.status != 0:
        if diag_dir:
            raise CodexExecFailure(result.status, diag_dir)
        raise SystemExit(command_failure(f"{command} failed", ProcessResult(args=args, status=result.status, stdout=result.stdout, stderr=result.stderr)))
    return CodexExecResult(
        status=result.status,
        stdout=result.stdout,
        stderr=result.stderr,
        final_message=final_message,
        output_path=stable_output_path,
    )


def last_matching_line(text: str, prefix: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith(prefix)]
    if not lines:
        raise ValueError(f"Missing required line starting with {prefix!r}: {text.strip()}")
    return lines[-1]


def parse_key_values(text: str) -> dict[str, str]:
    return {
        match.group(1): match.group(2) or match.group(3) or match.group(4) or ""
        for match in re.finditer(r'([A-Za-z][A-Za-z0-9_]*)=(?:"([^"]*)"|\'([^\']*)\'|(\S+))', text)
    }


def parse_session_id(jsonl: str) -> str | None:
    for line in jsonl.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parsed = parse_json_object(stripped)
        session_id = find_session_id(parsed)
        if session_id:
            return session_id
    match = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", jsonl, flags=re.IGNORECASE)
    return match.group(0) if match else None


def parse_json_object(value: str) -> object | None:
    try:
        return json.loads(value)
    except ValueError:
        return None


def find_session_id(value: object | None) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("session_id", "sessionId", "thread_id", "threadId", "conversation_id", "conversationId"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    for child in value.values():
        if isinstance(child, str):
            match = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", child, flags=re.IGNORECASE)
            if match:
                return match.group(0)
        nested = find_session_id(child)
        if nested:
            return nested
    return None


def fence(value: str, language: str = "text") -> str:
    return "\n".join([f"```{language}", value.strip(), "```"])


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def elapsed_seconds(started: datetime) -> int:
    return max(0, int((datetime.now(timezone.utc) - started).total_seconds()))


def output_to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_metadata(path: Path, value: dict) -> None:
    write_json(path / "metadata.json", value)
