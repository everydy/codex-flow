from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
import tempfile

from .git_ops import ProcessResult, command_failure, run_process


CODEX_CLI_MODEL = "gpt-5.5"
CODEX_CLI_REASONING_EFFORT = "xhigh"
CODEX_CLI_SERVICE_TIER = "fast"


@dataclass(frozen=True)
class CodexExecResult:
    status: int
    stdout: str
    stderr: str
    final_message: str
    output_path: Path


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
) -> CodexExecResult:
    repo_path = Path(repo).expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="codex-flow-agent-") as temp_dir:
        output_path = Path(temp_dir) / "last-message.txt"
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
        result = run_process(args, cwd=repo_path, input_text=prompt)
        final_message = output_path.read_text(encoding="utf-8") if output_path.exists() else result.stdout
        with tempfile.NamedTemporaryFile(prefix="codex-flow-last-message-", suffix=".txt", delete=False) as stable_file:
            stable_output_path = Path(stable_file.name)
        stable_output_path.write_text(final_message, encoding="utf-8")

    if result.status != 0:
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
