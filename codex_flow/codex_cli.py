from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
import tomllib

from .git_ops import ProcessResult, command_failure, run_process


CODEX_FLOW_MODEL_ENV = "CODEX_FLOW_MODEL"
CHILD_ISOLATION_ENV = "CODEX_FLOW_CHILD_ISOLATION"
CHILD_HOME_ENV = "CODEX_FLOW_CHILD_HOME"
CHILD_MANIFEST_ENV = "CODEX_FLOW_CHILD_MANIFEST"
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


class ChildAttestationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChildRuntimeAttestation:
    nonce: str
    child_home: str
    manifest_path: str
    manifest_sha256: str
    plan_path: str
    plan_sha256: str
    runtime_tree_sha256: str
    plugin_tree_sha256: str
    codex_cli_version: str
    discovery_command: tuple[str, ...]
    loaded_skills: tuple[str, ...]
    created_at: str
    ttl_seconds: int
    binding_sha256: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "nonce": self.nonce,
            "child_home": self.child_home,
            "manifest_path": self.manifest_path,
            "manifest_sha256": self.manifest_sha256,
            "plan_path": self.plan_path,
            "plan_sha256": self.plan_sha256,
            "runtime_tree_sha256": self.runtime_tree_sha256,
            "plugin_tree_sha256": self.plugin_tree_sha256,
            "codex_cli_version": self.codex_cli_version,
            "discovery_command": list(self.discovery_command),
            "loaded_skills": list(self.loaded_skills),
            "created_at": self.created_at,
            "ttl_seconds": self.ttl_seconds,
            "binding_sha256": self.binding_sha256,
        }


@dataclass(frozen=True)
class ChildClosureManifest:
    path: Path
    sha256: str
    skill_ids: tuple[str, ...]
    runtime_tree_sha256: str
    plugin_tree_sha256: str


def path_tree_hash(path: str | Path) -> str:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise ChildAttestationError(f"manifest tree path does not exist: {root}")
    digest = hashlib.sha256()
    paths = [root] if root.is_file() else [root, *sorted(root.rglob("*"), key=lambda item: item.as_posix())]
    for item in paths:
        relative = "." if item == root else item.relative_to(root).as_posix()
        if item.is_symlink():
            kind = "symlink"
            payload = os.readlink(item).encode("utf-8")
        elif item.is_dir():
            kind = "directory"
            payload = b""
        elif item.is_file():
            kind = "file"
            payload = item.read_bytes()
        else:
            raise ChildAttestationError(f"unsupported manifest tree entry: {item}")
        digest.update(kind.encode("utf-8") + b"\0" + relative.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def load_child_closure_manifest(child_home: str | Path, manifest_path: str | Path) -> ChildClosureManifest:
    home = Path(child_home).expanduser().resolve()
    path = Path(manifest_path).expanduser().resolve()
    if not path.is_file():
        raise ChildAttestationError(f"exact child manifest is missing: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ChildAttestationError(f"cannot read exact child manifest: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ChildAttestationError("exact child manifest must use version 1")
    skills = validate_manifest_entries(home, data.get("skills"), "skills")
    plugins = validate_manifest_entries(home, data.get("plugins"), "plugins")
    assert_exact_manifest_directory(home, skills, "skills")
    assert_exact_manifest_directory(home, plugins, "plugins")
    config_path = home / "config.toml"
    if not config_path.is_file():
        raise ChildAttestationError(f"prepared child runtime config is missing: {config_path}")
    runtime_payload = {
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "skills": skills,
    }
    return ChildClosureManifest(
        path=path,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        skill_ids=tuple(item[0] for item in skills),
        runtime_tree_sha256=hashlib.sha256(canonical_json(runtime_payload)).hexdigest(),
        plugin_tree_sha256=hashlib.sha256(canonical_json({"plugins": plugins})).hexdigest(),
    )


def validate_manifest_entries(home: Path, value: object, label: str) -> tuple[tuple[str, str, str], ...]:
    if not isinstance(value, list):
        raise ChildAttestationError(f"exact child manifest {label} must be a list")
    entries: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ChildAttestationError(f"exact child manifest {label} entry must be an object")
        item_id = str(raw.get("id") or "").strip()
        relative = str(raw.get("path") or "").strip()
        expected_hash = str(raw.get("sha256") or "").strip().lower()
        if not item_id or item_id in seen:
            raise ChildAttestationError(f"exact child manifest has an empty or duplicate {label} id: {item_id!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ChildAttestationError(f"exact child manifest has an invalid digest for {item_id}")
        item_path = (home / relative).resolve()
        if Path(relative).is_absolute() or not is_relative_to(item_path, home):
            raise ChildAttestationError(f"exact child manifest path escapes child home: {relative}")
        actual_hash = path_tree_hash(item_path)
        if actual_hash != expected_hash:
            raise ChildAttestationError(f"exact child manifest digest mismatch for {item_id}")
        seen.add(item_id)
        entries.append((item_id, relative, actual_hash))
    return tuple(entries)


def assert_exact_manifest_directory(
    home: Path,
    entries: tuple[tuple[str, str, str], ...],
    label: str,
) -> None:
    root = home / label
    declared: set[str] = set()
    for item_id, relative, _ in entries:
        parts = Path(relative).parts
        if len(parts) != 2 or parts[0] != label:
            raise ChildAttestationError(
                f"exact child manifest path for {item_id} must be a direct child of {label}/"
            )
        declared.add(parts[1])
    actual = set()
    if root.exists():
        actual = {path.name for path in root.iterdir()}
    if actual != declared:
        raise ChildAttestationError(
            f"exact child {label} closure mismatch: manifest={sorted(declared)} actual={sorted(actual)}"
        )


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def attestation_digest(attestation: ChildRuntimeAttestation) -> str:
    payload = attestation.to_dict()
    payload.pop("binding_sha256", None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def child_discovery_command(
    repo: str | Path,
    command: str,
    extra_args: list[str] | None,
    output_path: str,
) -> tuple[str, ...]:
    return (
        command,
        "exec",
        "--json",
        "--cd",
        str(Path(repo).expanduser().resolve()),
        "--sandbox",
        "read-only",
        "--output-last-message",
        output_path,
        *with_codex_cli_defaults(extra_args),
        "-",
    )


def normalized_discovery_command(repo: str | Path, command: str, extra_args: list[str] | None) -> tuple[str, ...]:
    return child_discovery_command(repo, command, extra_args, "<output-last-message>")


def codex_cli_version(
    command: str,
    env: dict[str, str],
    cwd: str | Path,
    timeout_seconds: int | None = None,
) -> str:
    try:
        result = run_process(
            [command, "--version"],
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ChildAttestationError("Codex CLI version discovery timed out") from exc
    if result.status != 0 or not result.stdout.strip():
        raise ChildAttestationError(command_failure("cannot read Codex CLI version", result))
    return result.stdout.strip().splitlines()[-1]


def generate_child_attestation(
    *,
    repo: str | Path,
    plan_path: str | Path,
    command: str = "codex",
    extra_args: list[str] | None = None,
    now: datetime | None = None,
    ttl_seconds: int = 300,
    timeout_seconds: int | None = None,
) -> ChildRuntimeAttestation:
    explicit_home = os.environ.get(CHILD_HOME_ENV, "").strip()
    manifest_value = os.environ.get(CHILD_MANIFEST_ENV, "").strip()
    if not explicit_home:
        raise ChildAttestationError(f"prepared child home is required in {CHILD_HOME_ENV}")
    if not manifest_value:
        raise ChildAttestationError(f"exact child manifest is required in {CHILD_MANIFEST_ENV}")
    if ttl_seconds <= 0:
        raise ChildAttestationError("child attestation TTL must be positive")
    repo_path = Path(repo).expanduser().resolve()
    approved_plan = Path(plan_path).expanduser().resolve()
    child_home = Path(explicit_home).expanduser().resolve()
    manifest = load_child_closure_manifest(child_home, manifest_value)
    child_env, metadata = child_runtime_environment(repo_path, extra_args=extra_args)
    if metadata.get("mode") != "isolated" or Path(metadata["home"]).resolve() != child_home:
        raise ChildAttestationError("prepared child home does not match the isolated child runtime")
    version = codex_cli_version(command, child_env, repo_path, timeout_seconds)
    nonce = secrets.token_hex(32)
    prompt = "\n".join(
        [
            "Discover the skills loaded in this fresh isolated Codex child runtime.",
            f"Attestation nonce: {nonce}",
            "Return only one JSON object with exactly these keys:",
            '{"nonce":"<the nonce above>","loaded_ids":["<every loaded skill id>"]}',
            "Do not infer skills from this prompt. Report only the skill ids available in your runtime context.",
        ]
    )
    with tempfile.TemporaryDirectory(prefix="codex-flow-child-discovery-") as temp_dir:
        output_path = Path(temp_dir) / "last-message.json"
        args = list(child_discovery_command(repo_path, command, extra_args, str(output_path)))
        try:
            result = run_process(
                args,
                cwd=repo_path,
                input_text=prompt,
                env=child_env,
                timeout_seconds=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ChildAttestationError("fresh child skill discovery timed out") from exc
        if result.status != 0:
            raise ChildAttestationError(command_failure("fresh child skill discovery failed", result))
        final_message = output_path.read_text(encoding="utf-8") if output_path.exists() else result.stdout
    discovered = parse_child_discovery(final_message, nonce)
    created = now or datetime.now(timezone.utc)
    unsigned = ChildRuntimeAttestation(
        nonce=nonce,
        child_home=str(child_home),
        manifest_path=str(manifest.path),
        manifest_sha256=manifest.sha256,
        plan_path=str(approved_plan),
        plan_sha256=hashlib.sha256(approved_plan.read_bytes()).hexdigest(),
        runtime_tree_sha256=manifest.runtime_tree_sha256,
        plugin_tree_sha256=manifest.plugin_tree_sha256,
        codex_cli_version=version,
        discovery_command=normalized_discovery_command(repo_path, command, extra_args),
        loaded_skills=discovered,
        created_at=created.astimezone(timezone.utc).isoformat(),
        ttl_seconds=ttl_seconds,
    )
    return replace(unsigned, binding_sha256=attestation_digest(unsigned))


def parse_child_discovery(text: str, expected_nonce: str) -> tuple[str, ...]:
    try:
        value = json.loads(text.strip())
    except ValueError as exc:
        raise ChildAttestationError("fresh child discovery did not return valid JSON") from exc
    if not isinstance(value, dict) or set(value) != {"nonce", "loaded_ids"}:
        raise ChildAttestationError("fresh child discovery returned an invalid schema")
    if value.get("nonce") != expected_nonce:
        raise ChildAttestationError("fresh child discovery nonce mismatch")
    loaded = value.get("loaded_ids")
    if not isinstance(loaded, list) or any(not isinstance(item, str) or not item.strip() for item in loaded):
        raise ChildAttestationError("fresh child discovery loaded_ids must be non-empty strings")
    normalized = tuple(item.strip() for item in loaded)
    if len(set(normalized)) != len(normalized):
        raise ChildAttestationError("fresh child discovery returned duplicate loaded ids")
    return normalized


def verify_child_attestation(
    attestation: ChildRuntimeAttestation,
    *,
    repo: str | Path,
    plan_path: str | Path,
    manifest_path: str | Path,
    required_skills: tuple[str, ...] | list[str],
    command: str = "codex",
    extra_args: list[str] | None = None,
    nonce_ledger: str | Path,
    now: datetime | None = None,
    timeout_seconds: int | None = None,
) -> ChildRuntimeAttestation:
    if attestation.binding_sha256 != attestation_digest(attestation):
        raise ChildAttestationError("child attestation binding digest mismatch")
    explicit_home = os.environ.get(CHILD_HOME_ENV, "").strip()
    if not explicit_home:
        raise ChildAttestationError(f"prepared child home is required in {CHILD_HOME_ENV}")
    child_home = Path(explicit_home).expanduser().resolve()
    manifest = load_child_closure_manifest(child_home, manifest_path)
    checks = (
        (attestation.child_home == str(child_home), "child path mismatch"),
        (attestation.manifest_path == str(manifest.path), "manifest path mismatch"),
        (attestation.manifest_sha256 == manifest.sha256, "manifest hash mismatch"),
        (attestation.plan_path == str(Path(plan_path).expanduser().resolve()), "plan path mismatch"),
        (attestation.plan_sha256 == hashlib.sha256(Path(plan_path).read_bytes()).hexdigest(), "plan hash mismatch"),
        (attestation.runtime_tree_sha256 == manifest.runtime_tree_sha256, "runtime tree hash mismatch"),
        (attestation.plugin_tree_sha256 == manifest.plugin_tree_sha256, "plugin tree hash mismatch"),
        (
            attestation.discovery_command == normalized_discovery_command(repo, command, extra_args),
            "discovery command mismatch",
        ),
    )
    for valid, message in checks:
        if not valid:
            raise ChildAttestationError(message)
    child_env, _ = child_runtime_environment(repo, extra_args=extra_args)
    if attestation.codex_cli_version != codex_cli_version(command, child_env, repo, timeout_seconds):
        raise ChildAttestationError("Codex CLI version mismatch")
    missing = set(required_skills) - set(attestation.loaded_skills)
    if missing:
        raise ChildAttestationError(f"missing required skills before edit: {', '.join(sorted(missing))}")
    if set(attestation.loaded_skills) != set(manifest.skill_ids):
        raise ChildAttestationError(
            "discovered skill closure mismatch: "
            f"manifest={sorted(manifest.skill_ids)} loaded={sorted(attestation.loaded_skills)}"
        )
    current = now or datetime.now(timezone.utc)
    try:
        created = datetime.fromisoformat(attestation.created_at)
    except ValueError as exc:
        raise ChildAttestationError("child attestation timestamp is invalid") from exc
    if created.tzinfo is None:
        raise ChildAttestationError("child attestation timestamp must be timezone-aware")
    age = (current.astimezone(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
    if age < 0 or age > attestation.ttl_seconds:
        raise ChildAttestationError("child attestation is stale")
    consume_attestation_nonce(nonce_ledger, attestation.nonce)
    return attestation


def consume_attestation_nonce(path: str | Path, nonce: str) -> None:
    ledger = Path(path).expanduser().resolve()
    claim_directory = ledger.parent / f".{ledger.name}.claims"
    claim_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    claim_path = claim_directory / hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    try:
        descriptor = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ChildAttestationError("replayed nonce in child attestation") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(nonce + "\n")

    used: list[str] = []
    if ledger.exists():
        try:
            value = json.loads(ledger.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ChildAttestationError(f"cannot read child attestation nonce ledger: {exc}") from exc
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ChildAttestationError("child attestation nonce ledger is invalid")
        used = value
    if nonce in used:
        raise ChildAttestationError("replayed nonce in child attestation")
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="used-nonces.",
        suffix=".json.tmp",
        dir=ledger.parent,
        delete=False,
    ) as handle:
        json.dump([*used, nonce], handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(ledger)


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
        write_sanitized_child_config(parent_home, child_home, repo_path)
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


def write_sanitized_child_config(parent_home: Path, child_home: Path, target_path: Path) -> None:
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
    projects = source.get("projects")
    if isinstance(projects, dict):
        for project_path, project_config in projects.items():
            if not isinstance(project_path, str) or not isinstance(project_config, dict):
                continue
            if Path(project_path).expanduser().resolve() != target_path:
                continue
            if project_config.get("trust_level") == "trusted":
                lines.extend(
                    [
                        f"[projects.{json.dumps(str(target_path), ensure_ascii=False)}]",
                        'trust_level = "trusted"',
                        "",
                    ]
                )
            break
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
