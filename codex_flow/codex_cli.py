from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import queue
import re
import secrets
import subprocess
import tempfile
import threading
import time
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
    external_skill_ids: tuple[str, ...]
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
            "external_skill_ids": list(self.external_skill_ids),
            "created_at": self.created_at,
            "ttl_seconds": self.ttl_seconds,
            "binding_sha256": self.binding_sha256,
        }


@dataclass(frozen=True)
class ChildClosureManifest:
    path: Path
    sha256: str
    skill_ids: tuple[str, ...]
    plugin_skill_ids: tuple[str, ...]
    external_skill_ids: tuple[str, ...]
    runtime_tree_sha256: str
    plugin_tree_sha256: str
    skill_roots: tuple[tuple[str, str], ...]
    plugin_roots: tuple[str, ...]


@dataclass(frozen=True)
class ChildSkillInventoryRecord:
    skill_id: str
    path: Path


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
    plugin_skill_ids = validate_plugin_skill_ids(data.get("plugins"))
    external_skill_ids = validate_skill_ids(data.get("external_skill_ids", []), "external_skill_ids")
    assert_distinct_skill_owners(
        tuple(item[0] for item in skills),
        plugin_skill_ids,
        external_skill_ids,
    )
    assert_exact_manifest_directory(home, skills, "skills")
    assert_exact_plugin_payloads(home, plugins)
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
        plugin_skill_ids=plugin_skill_ids,
        external_skill_ids=external_skill_ids,
        runtime_tree_sha256=hashlib.sha256(canonical_json(runtime_payload)).hexdigest(),
        plugin_tree_sha256=hashlib.sha256(
            canonical_json(
                {
                    "plugins": plugins,
                    "plugin_skill_ids": plugin_skill_ids,
                }
            )
        ).hexdigest(),
        skill_roots=tuple((item[0], str((home / item[1]).resolve())) for item in skills),
        plugin_roots=tuple(str((home / item[1]).resolve()) for item in plugins),
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


def validate_plugin_skill_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ChildAttestationError("exact child manifest plugins must be a list")
    skill_ids: list[str] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ChildAttestationError("exact child manifest plugins entry must be an object")
        plugin_id = str(raw.get("id") or "").strip()
        skill_ids.extend(validate_skill_ids(raw.get("skill_ids", []), f"plugin {plugin_id} skill_ids"))
    if len(set(skill_ids)) != len(skill_ids):
        raise ChildAttestationError("exact child manifest has duplicate plugin skill ids")
    return tuple(skill_ids)


def validate_skill_ids(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ChildAttestationError(f"exact child manifest {label} must be a list")
    normalized: list[str] = []
    for raw in value:
        if not isinstance(raw, str) or not raw.strip():
            raise ChildAttestationError(
                f"exact child manifest {label} must contain non-empty strings"
            )
        normalized.append(raw.strip())
    if len(set(normalized)) != len(normalized):
        raise ChildAttestationError(f"exact child manifest {label} has duplicate ids")
    return tuple(normalized)


def assert_distinct_skill_owners(
    child_skill_ids: tuple[str, ...],
    plugin_skill_ids: tuple[str, ...],
    external_skill_ids: tuple[str, ...],
) -> None:
    ownership = {
        "child": set(child_skill_ids),
        "plugin": set(plugin_skill_ids),
        "external": set(external_skill_ids),
    }
    overlaps = (
        ownership["child"] & ownership["plugin"]
        | ownership["child"] & ownership["external"]
        | ownership["plugin"] & ownership["external"]
    )
    if overlaps:
        raise ChildAttestationError(
            f"exact child manifest has ambiguous skill ownership: {sorted(overlaps)}"
        )


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


def assert_exact_plugin_payloads(
    home: Path,
    entries: tuple[tuple[str, str, str], ...],
) -> None:
    root = home / "plugins"
    declared_direct: set[str] = set()
    declared_cached: set[str] = set()
    for item_id, relative, _ in entries:
        parts = Path(relative).parts
        if len(parts) == 2 and parts[0] == "plugins" and parts[1] not in {
            "cache",
            ".remote-plugin-install-staging",
        }:
            declared_direct.add(relative)
            continue
        if len(parts) == 5 and parts[:2] == ("plugins", "cache"):
            declared_cached.add(relative)
            continue
        raise ChildAttestationError(
            "exact child manifest path for "
            f"{item_id} must identify a plugin payload root below plugins/"
        )

    actual_direct: set[str] = set()
    if root.exists():
        actual_direct.update(
            path.relative_to(home).as_posix()
            for path in root.iterdir()
            if path.name not in {"cache", ".remote-plugin-install-staging"}
        )
    actual_cached: set[str] = set()
    selected_plugin_roots = {home / Path(relative).parent for relative in declared_cached}
    for plugin_root in selected_plugin_roots:
        actual_cached.update(
            path.relative_to(home).as_posix()
            for path in plugin_root.iterdir()
            if path.is_dir()
        )
    declared = declared_direct | declared_cached
    actual = actual_direct | actual_cached
    if actual != declared:
        raise ChildAttestationError(
            f"exact child plugins closure mismatch: manifest={sorted(declared)} actual={sorted(actual)}"
        )


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def attestation_digest(attestation: ChildRuntimeAttestation) -> str:
    payload = attestation.to_dict()
    payload.pop("binding_sha256", None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def child_discovery_command(command: str) -> tuple[str, ...]:
    return (command, "app-server", "--listen", "stdio://")


def normalized_discovery_command(repo: str | Path, command: str, extra_args: list[str] | None) -> tuple[str, ...]:
    return child_discovery_command(command)


def inventory_skill_records(
    response: object,
    *,
    repo: Path,
) -> tuple[ChildSkillInventoryRecord, ...]:
    if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
        raise ChildAttestationError("app-server skills/list response has no result")
    data = response["result"].get("data")
    if not isinstance(data, list):
        raise ChildAttestationError("app-server skills/list result has no data list")
    matches = [
        entry
        for entry in data
        if isinstance(entry, dict)
        and isinstance(entry.get("cwd"), str)
        and Path(entry["cwd"]).expanduser().resolve() == repo
    ]
    if len(matches) != 1:
        raise ChildAttestationError(
            f"app-server skills/list returned {len(matches)} entries for execution cwd"
        )
    entry = matches[0]
    errors = entry.get("errors")
    skills = entry.get("skills")
    if not isinstance(errors, list) or errors:
        raise ChildAttestationError("app-server skills/list reported discovery errors")
    if not isinstance(skills, list):
        raise ChildAttestationError("app-server skills/list cwd entry has no skills list")
    records: list[ChildSkillInventoryRecord] = []
    for skill in skills:
        if not isinstance(skill, dict) or skill.get("enabled") is not True:
            continue
        name = skill.get("name")
        path = skill.get("path")
        if not isinstance(name, str) or not name.strip() or not isinstance(path, str):
            raise ChildAttestationError("app-server returned an invalid enabled skill record")
        records.append(
            ChildSkillInventoryRecord(
                skill_id=name.strip(),
                path=Path(path).expanduser().resolve(),
            )
        )
    if len({record.skill_id for record in records}) != len(records):
        raise ChildAttestationError("app-server returned duplicate enabled skill ids")
    return tuple(records)


def validate_inventory_ownership(
    records: tuple[ChildSkillInventoryRecord, ...],
    manifest: ChildClosureManifest,
) -> tuple[str, ...]:
    skill_roots = {skill_id: Path(path) for skill_id, path in manifest.skill_roots}
    plugin_roots = tuple(Path(path) for path in manifest.plugin_roots)
    for record in records:
        if record.skill_id in skill_roots and not is_relative_to(
            record.path, skill_roots[record.skill_id]
        ):
            raise ChildAttestationError(
                f"child skill path ownership mismatch: {record.skill_id}"
            )
        if record.skill_id in manifest.plugin_skill_ids and not any(
            is_relative_to(record.path, root) for root in plugin_roots
        ):
            raise ChildAttestationError(
                f"plugin skill path ownership mismatch: {record.skill_id}"
            )
    return tuple(record.skill_id for record in records)


def discover_child_skills(
    *,
    repo: Path,
    child_home: Path,
    manifest: ChildClosureManifest,
    command: str,
    env: dict[str, str],
    timeout_seconds: int | None,
) -> tuple[str, ...]:
    records = discover_child_skill_records(
        repo=repo,
        child_home=child_home,
        command=command,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    return validate_inventory_ownership(records, manifest)


def discover_child_skill_records(
    *,
    repo: Path,
    child_home: Path,
    command: str,
    env: dict[str, str],
    timeout_seconds: int | None,
) -> tuple[ChildSkillInventoryRecord, ...]:
    args = list(child_discovery_command(command))
    try:
        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            cwd=repo,
        )
    except OSError as exc:
        raise ChildAttestationError(f"app-server skill discovery could not start: {exc}") from exc
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        process.wait()
        raise ChildAttestationError("app-server did not expose stdio pipes")
    responses: queue.Queue[str | None] = queue.Queue()
    stderr_lines: list[str] = []

    def read_stdout() -> None:
        for line in process.stdout:
            responses.put(line)
        responses.put(None)

    def read_stderr() -> None:
        stderr_lines.extend(process.stderr.readlines())

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    deadline = time.monotonic() + float(timeout_seconds or 30)
    protocol_complete = False

    def send(request: dict[str, object]) -> None:
        process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def receive(request_id: int, method: str) -> dict[str, object]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ChildAttestationError(f"app-server {method} timed out")
            try:
                line = responses.get(timeout=remaining)
            except queue.Empty as exc:
                raise ChildAttestationError(f"app-server {method} timed out") from exc
            if line is None:
                detail = "".join(stderr_lines).strip()
                suffix = f": {detail}" if detail else ""
                raise ChildAttestationError(
                    f"app-server exited before the {method} response{suffix}"
                )
            try:
                message = json.loads(line)
            except ValueError as exc:
                raise ChildAttestationError(
                    f"app-server returned malformed JSON during {method}"
                ) from exc
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if "error" in message:
                raise ChildAttestationError(f"app-server {method} protocol error: {message['error']}")
            return message

    try:
        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "codex-flow", "version": "1"}},
            }
        )
        initialized = receive(1, "initialize")
        result = initialized.get("result")
        codex_home = result.get("codexHome") if isinstance(result, dict) else None
        if not isinstance(codex_home, str) or Path(codex_home).expanduser().resolve() != child_home:
            raise ChildAttestationError(
                "app-server initialize response did not match prepared CODEX_HOME"
            )
        send(
            {
                "id": 2,
                "method": "skills/list",
                "params": {"cwds": [str(repo)], "forceReload": True},
            }
        )
        inventory = inventory_skill_records(receive(2, "skills/list"), repo=repo)
        protocol_complete = True
        return inventory
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        stdout_thread.join(timeout=0.1)
        stderr_thread.join(timeout=0.1)
        if protocol_complete and process.returncode != 0:
            detail = "".join(stderr_lines).strip()
            suffix = f": {detail}" if detail else ""
            raise ChildAttestationError(
                f"app-server exited {process.returncode} after skills/list{suffix}"
            )


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
    child_home: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> ChildRuntimeAttestation:
    explicit_home = (
        str(child_home)
        if child_home is not None
        else os.environ.get(CHILD_HOME_ENV, "").strip()
    )
    manifest_value = (
        str(manifest_path)
        if manifest_path is not None
        else os.environ.get(CHILD_MANIFEST_ENV, "").strip()
    )
    if not explicit_home:
        raise ChildAttestationError(f"prepared child home is required in {CHILD_HOME_ENV}")
    if not manifest_value:
        raise ChildAttestationError(f"exact child manifest is required in {CHILD_MANIFEST_ENV}")
    if ttl_seconds <= 0:
        raise ChildAttestationError("child attestation TTL must be positive")
    repo_path = Path(repo).expanduser().resolve()
    approved_plan = Path(plan_path).expanduser().resolve()
    resolved_child_home = Path(explicit_home).expanduser().resolve()
    manifest = load_child_closure_manifest(resolved_child_home, manifest_value)
    child_env, metadata = child_runtime_environment(
        repo_path, extra_args=extra_args, child_home=resolved_child_home
    )
    if metadata.get("mode") != "isolated" or Path(metadata["home"]).resolve() != resolved_child_home:
        raise ChildAttestationError("prepared child home does not match the isolated child runtime")
    version = codex_cli_version(command, child_env, repo_path, timeout_seconds)
    nonce = secrets.token_hex(32)
    discovered = discover_child_skills(
        repo=repo_path,
        child_home=resolved_child_home,
        manifest=manifest,
        command=command,
        env=child_env,
        timeout_seconds=timeout_seconds,
    )
    created = now or datetime.now(timezone.utc)
    unsigned = ChildRuntimeAttestation(
        nonce=nonce,
        child_home=str(resolved_child_home),
        manifest_path=str(manifest.path),
        manifest_sha256=manifest.sha256,
        plan_path=str(approved_plan),
        plan_sha256=hashlib.sha256(approved_plan.read_bytes()).hexdigest(),
        runtime_tree_sha256=manifest.runtime_tree_sha256,
        plugin_tree_sha256=manifest.plugin_tree_sha256,
        codex_cli_version=version,
        discovery_command=normalized_discovery_command(repo_path, command, extra_args),
        loaded_skills=discovered,
        external_skill_ids=manifest.external_skill_ids,
        created_at=created.astimezone(timezone.utc).isoformat(),
        ttl_seconds=ttl_seconds,
    )
    return replace(unsigned, binding_sha256=attestation_digest(unsigned))


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
    child_home: str | Path | None = None,
) -> ChildRuntimeAttestation:
    if attestation.binding_sha256 != attestation_digest(attestation):
        raise ChildAttestationError("child attestation binding digest mismatch")
    explicit_home = (
        str(child_home)
        if child_home is not None
        else os.environ.get(CHILD_HOME_ENV, "").strip()
    )
    if not explicit_home:
        raise ChildAttestationError(f"prepared child home is required in {CHILD_HOME_ENV}")
    resolved_child_home = Path(explicit_home).expanduser().resolve()
    manifest = load_child_closure_manifest(resolved_child_home, manifest_path)
    checks = (
        (attestation.child_home == str(resolved_child_home), "child path mismatch"),
        (attestation.manifest_path == str(manifest.path), "manifest path mismatch"),
        (attestation.manifest_sha256 == manifest.sha256, "manifest hash mismatch"),
        (attestation.plan_path == str(Path(plan_path).expanduser().resolve()), "plan path mismatch"),
        (attestation.plan_sha256 == hashlib.sha256(Path(plan_path).read_bytes()).hexdigest(), "plan hash mismatch"),
        (attestation.runtime_tree_sha256 == manifest.runtime_tree_sha256, "runtime tree hash mismatch"),
        (attestation.plugin_tree_sha256 == manifest.plugin_tree_sha256, "plugin tree hash mismatch"),
        (
            attestation.external_skill_ids == manifest.external_skill_ids,
            "external skill ids mismatch",
        ),
        (
            attestation.discovery_command == normalized_discovery_command(repo, command, extra_args),
            "discovery command mismatch",
        ),
    )
    for valid, message in checks:
        if not valid:
            raise ChildAttestationError(message)
    child_env, _ = child_runtime_environment(
        repo, extra_args=extra_args, child_home=resolved_child_home
    )
    if attestation.codex_cli_version != codex_cli_version(command, child_env, repo, timeout_seconds):
        raise ChildAttestationError("Codex CLI version mismatch")
    loaded_skills = set(attestation.loaded_skills)
    missing = {
        required
        for required in required_skills
        if required not in loaded_skills
        and len(
            [
                skill_id
                for skill_id in (*manifest.plugin_skill_ids, *manifest.external_skill_ids)
                if skill_id.rsplit(":", 1)[-1] == required and skill_id in loaded_skills
            ]
        )
        != 1
    }
    if missing:
        raise ChildAttestationError(f"missing required skills before edit: {', '.join(sorted(missing))}")
    expected_loaded_skills = (
        *manifest.skill_ids,
        *manifest.plugin_skill_ids,
        *manifest.external_skill_ids,
    )
    if set(attestation.loaded_skills) != set(expected_loaded_skills):
        raise ChildAttestationError(
            "discovered skill closure mismatch: "
            f"manifest={sorted(expected_loaded_skills)} loaded={sorted(attestation.loaded_skills)}"
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
    child_home: str | Path | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    repo_path = Path(repo).expanduser().resolve()
    parent_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()
    env = {**os.environ, **MACHINE_READABLE_AGENT_ENV}
    if os.environ.get(CHILD_ISOLATION_ENV, "1").strip().lower() in {"0", "false", "no", "off"}:
        env["CODEX_HOME"] = str(parent_home)
        return env, {"mode": "disabled", "source": CHILD_ISOLATION_ENV, "home": str(parent_home)}

    parameter_home = child_home is not None
    explicit_home = (
        str(child_home)
        if parameter_home
        else os.environ.get(CHILD_HOME_ENV, "").strip()
    )
    if has_explicit_profile_arg(extra_args or []) and not explicit_home:
        raise ChildRuntimeConfigError(
            "child isolation cannot safely copy an explicit Codex profile; set "
            f"{CHILD_HOME_ENV} to a prepared child home or set {CHILD_ISOLATION_ENV}=0"
        )

    if explicit_home:
        child_home = Path(explicit_home).expanduser().resolve()
        source = "parameter" if parameter_home else CHILD_HOME_ENV
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
    child_home: str | Path | None = None,
) -> CodexExecResult:
    repo_path = Path(repo).expanduser().resolve()
    model_selection = model_selection_metadata(extra_args)
    child_env, child_runtime = child_runtime_environment(
        repo_path, extra_args=extra_args, child_home=child_home
    )
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
