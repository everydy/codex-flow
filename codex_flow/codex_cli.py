from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import tempfile
import tomllib

from .git_ops import ProcessResult, command_failure, run_process


CODEX_FLOW_MODEL_ENV = "CODEX_FLOW_MODEL"
CHILD_ISOLATION_ENV = "CODEX_FLOW_CHILD_ISOLATION"
CHILD_HOME_ENV = "CODEX_FLOW_CHILD_HOME"
CHILD_RUNTIME_ROOT_ENV = "CODEX_CHILD_RUNTIME_ROOT"
MACHINE_READABLE_AGENT_ENV = {
    "CODEX_BOOTSTRAP_HOOK_DISABLED": "1",
    "CODEX_REQUEST_REFINER_HOOK_DISABLED": "1",
    "CODEX_CLOSEOUT_HOOK_DISABLED": "1",
}
SAFE_CHILD_CONFIG_KEYS = ("model", "model_reasoning_effort", "service_tier", "model_provider")


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


class ChildRuntimeConfigError(RuntimeError):
    pass


def child_runtime_environment(
    repo: str | Path,
    *,
    extra_args: list[str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    repo_path = Path(repo).expanduser().resolve()
    parent_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()
    env = {**os.environ, **MACHINE_READABLE_AGENT_ENV}
    if os.environ.get(CHILD_ISOLATION_ENV, "1").strip().lower() in {"0", "false", "no", "off"}:
        env["CODEX_HOME"] = str(parent_home)
        return env, {"mode": "disabled", "source": CHILD_ISOLATION_ENV, "home": str(parent_home)}

    explicit_home = os.environ.get(CHILD_HOME_ENV, "").strip()
    if has_explicit_profile_arg(extra_args or []) and not explicit_home:
        raise ChildRuntimeConfigError(
            "child isolation cannot safely copy an explicit Codex profile; set "
            f"{CHILD_HOME_ENV} to a prepared child home or set {CHILD_ISOLATION_ENV}=0"
        )

    if explicit_home:
        child_home = Path(explicit_home).expanduser().resolve()
        source = CHILD_HOME_ENV
        managed_config = False
    else:
        runtime_root = Path(
            os.environ.get(CHILD_RUNTIME_ROOT_ENV, str(parent_home / "child-runtimes"))
        ).expanduser().resolve()
        namespace = hashlib.sha256(str(repo_path).encode("utf-8")).hexdigest()[:16]
        child_home = runtime_root / "codex-flow" / namespace
        source = CHILD_RUNTIME_ROOT_ENV if os.environ.get(CHILD_RUNTIME_ROOT_ENV) else "default"
        managed_config = True

    if child_home == parent_home:
        raise ChildRuntimeConfigError("isolated Codex child home must differ from the parent CODEX_HOME")
    if is_relative_to(child_home, repo_path):
        raise ChildRuntimeConfigError(
            f"isolated Codex child home must stay outside the target repository: {child_home}"
        )
    if managed_config:
        ensure_private_directory(child_home.parent.parent)
        ensure_private_directory(child_home.parent)
    ensure_private_directory(child_home)
    link_child_auth(parent_home, child_home)
    if managed_config:
        write_sanitized_child_config(parent_home, child_home)
    env["CODEX_HOME"] = str(child_home)
    return env, {"mode": "isolated", "source": source, "home": str(child_home)}


def has_explicit_profile_arg(args: list[str]) -> bool:
    return any(arg in {"--profile", "-p"} or arg.startswith("--profile=") or arg.startswith("-p=") for arg in args)


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def link_child_auth(parent_home: Path, child_home: Path) -> None:
    parent_auth = parent_home / "auth.json"
    child_auth = child_home / "auth.json"
    if child_auth.exists() or child_auth.is_symlink() or not parent_auth.exists():
        return
    child_auth.symlink_to(parent_auth)


def write_sanitized_child_config(parent_home: Path, child_home: Path) -> None:
    source_path = parent_home / "config.toml"
    source: dict[str, object] = {}
    if source_path.exists():
        try:
            with source_path.open("rb") as handle:
                parsed = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ChildRuntimeConfigError(f"cannot read parent Codex config: {exc}") from exc
        source = parsed
    provider = str(source.get("model_provider", "")).strip()
    if provider and provider != "openai":
        raise ChildRuntimeConfigError(
            "child isolation cannot safely sanitize a custom model provider; "
            f"set {CHILD_HOME_ENV} to a prepared child home or set {CHILD_ISOLATION_ENV}=0"
        )
    lines: list[str] = []
    for key in SAFE_CHILD_CONFIG_KEYS:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
    if lines:
        lines.append("")
    lines.extend(["[skills.bundled]", "enabled = false", ""])
    target = child_home / "config.toml"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="config.",
        suffix=".toml.tmp",
        dir=child_home,
        delete=False,
    ) as handle:
        handle.write("\n".join(lines))
        temp_path = Path(handle.name)
    os.chmod(temp_path, 0o600)
    temp_path.replace(target)


def codex_cli_default_args(*, include_model: bool = True) -> list[str]:
    args: list[str] = []
    requested_model = os.environ.get(CODEX_FLOW_MODEL_ENV, "").strip()
    if include_model and requested_model:
        args.extend(["--model", requested_model])
    return args


def with_codex_cli_defaults(extra_args: list[str] | None = None) -> list[str]:
    provided_args = extra_args or []
    return [*codex_cli_default_args(include_model=not has_explicit_model_arg(provided_args)), *provided_args]


def has_explicit_model_arg(args: list[str]) -> bool:
    return any(arg == "--model" or arg.startswith("--model=") or arg == "-m" or arg.startswith("-m=") for arg in args)


def explicit_model_arg(args: list[str]) -> str | None:
    requested: str | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--model", "-m"}:
            if index + 1 < len(args):
                requested = args[index + 1]
                index += 2
                continue
        elif arg.startswith("--model=") or arg.startswith("-m="):
            requested = arg.split("=", 1)[1]
            index += 1
            continue
        index += 1
    return requested


def model_selection_metadata(extra_args: list[str] | None) -> dict[str, str | None]:
    explicit = explicit_model_arg(extra_args or [])
    if explicit is not None:
        return {"source": "explicit", "requested": explicit}
    environment = os.environ.get(CODEX_FLOW_MODEL_ENV, "").strip()
    if environment:
        return {"source": "environment", "requested": environment}
    return {"source": "inherited", "requested": None}


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
    model_selection = model_selection_metadata(extra_args)
    child_env, child_runtime = child_runtime_environment(repo_path, extra_args=extra_args)
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
                    "model_selection": model_selection,
                    "child_runtime": child_runtime,
                },
            )
        started = datetime.now(timezone.utc)
        result: ProcessResult | None = None
        try:
            result = run_process(
                args,
                cwd=repo_path,
                input_text=prompt,
                env=child_env,
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
                        "model_selection": model_selection,
                        "child_runtime": child_runtime,
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
                    "model_selection": model_selection,
                    "child_runtime": child_runtime,
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
