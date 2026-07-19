from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
import threading

from .codex_cli import (
    CHILD_HOME_ENV,
    CHILD_ISOLATION_ENV,
    CHILD_MANIFEST_ENV,
    CHILD_RUNTIME_ROOT_ENV,
    MACHINE_READABLE_AGENT_ENV,
    ChildAttestationError,
    ChildClosureManifest,
    ChildRuntimeConfigError,
    ChildSkillInventoryRecord,
    canonical_json,
    codex_cli_version,
    discover_child_skill_records,
    ensure_private_directory,
    has_explicit_profile_arg,
    is_relative_to,
    link_child_auth,
    load_child_closure_manifest,
    path_tree_hash,
    write_sanitized_child_config,
)


MANIFEST_NAME = "child-closure-manifest.json"
MANIFEST_SCHEMA_VERSION = 1
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


@dataclass(frozen=True)
class PreparedChildRuntime:
    home: Path
    manifest: Path
    source: str
    cache_key: str
    reused: bool


def ensure_prepared_child_runtime(
    *,
    repo: str | Path,
    required_skills: tuple[str, ...] | list[str],
    command: str = "codex",
    extra_args: list[str] | None = None,
    timeout_seconds: int | None = None,
) -> PreparedChildRuntime:
    repo_path = Path(repo).expanduser().resolve()
    required = _normalize_required_skills(required_skills)
    explicit_home = os.environ.get(CHILD_HOME_ENV, "").strip()
    explicit_manifest = os.environ.get(CHILD_MANIFEST_ENV, "").strip()
    if bool(explicit_home) != bool(explicit_manifest):
        raise ChildRuntimeConfigError(
            f"{CHILD_HOME_ENV} and {CHILD_MANIFEST_ENV} must both be set or both be unset"
        )
    if explicit_home:
        return _validate_explicit_runtime(
            repo_path, required, Path(explicit_home), Path(explicit_manifest)
        )
    if has_explicit_profile_arg(extra_args or []):
        raise ChildRuntimeConfigError(
            "managed child preparation cannot safely copy an explicit Codex profile; "
            f"set {CHILD_HOME_ENV} and {CHILD_MANIFEST_ENV} to a prepared override"
        )
    if os.environ.get(CHILD_ISOLATION_ENV, "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        raise ChildRuntimeConfigError("managed child preparation requires child isolation")

    parent_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()
    runtime_root = Path(
        os.environ.get(CHILD_RUNTIME_ROOT_ENV, str(parent_home / "child-runtimes"))
    ).expanduser().resolve()
    if is_relative_to(runtime_root, repo_path):
        raise ChildRuntimeConfigError(
            f"managed child runtime root must stay outside the target repository: {runtime_root}"
        )
    sources = _resolve_standalone_sources(parent_home, repo_path, required)
    repo_key = hashlib.sha256(str(repo_path).encode("utf-8")).hexdigest()[:16]
    repo_root = runtime_root / "codex-flow" / repo_key
    staging_root = repo_root / ".staging"
    ensure_private_directory(runtime_root)
    ensure_private_directory(runtime_root / "codex-flow")
    ensure_private_directory(repo_root)
    ensure_private_directory(staging_root)

    stage = Path(tempfile.mkdtemp(prefix="prepare-", dir=staging_root))
    os.chmod(stage, 0o700)
    try:
        link_child_auth(parent_home, stage)
        write_sanitized_child_config(parent_home, stage, repo_path)
        skills_root = stage / "skills"
        if sources:
            ensure_private_directory(skills_root)
        source_entries: list[dict[str, str]] = []
        for skill_id, source in sources:
            target = skills_root / skill_id
            shutil.copytree(source, target)
            source_entries.append(
                {
                    "id": skill_id,
                    "path": f"skills/{skill_id}",
                    "sha256": path_tree_hash(target),
                }
            )

        child_env = {**os.environ, **MACHINE_READABLE_AGENT_ENV, "CODEX_HOME": str(stage)}
        version = codex_cli_version(command, child_env, repo_path, timeout_seconds)
        records = discover_child_skill_records(
            repo=repo_path,
            child_home=stage,
            command=command,
            env=child_env,
            timeout_seconds=timeout_seconds,
        )
        plugins, external_ids = _classify_inventory(stage, sources, records)
        _assert_required_present(required, records)
        _remove_volatile_children(stage)

        payload = {
            "version": MANIFEST_SCHEMA_VERSION,
            "skills": sorted(source_entries, key=lambda item: item["id"]),
            "plugins": plugins,
            "external_skill_ids": sorted(external_ids),
        }
        config_digest = hashlib.sha256((stage / "config.toml").read_bytes()).hexdigest()
        cache_payload = {
            "schema": MANIFEST_SCHEMA_VERSION,
            "repo": str(repo_path),
            "required": required,
            "sources": [(item["id"], item["sha256"]) for item in payload["skills"]],
            "plugins": [
                (item["id"], item["sha256"], item["skill_ids"])
                for item in payload["plugins"]
            ],
            "external_skill_ids": payload["external_skill_ids"],
            "config_sha256": config_digest,
            "codex_cli_version": version,
            "command": command,
            "extra_args": list(extra_args or []),
        }
        cache_key = hashlib.sha256(canonical_json(cache_payload)).hexdigest()
        manifest = stage / MANIFEST_NAME
        manifest.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(manifest, 0o600)
        load_child_closure_manifest(stage, manifest)
        return _publish_runtime(repo_root, stage, cache_key, required)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _normalize_required_skills(required_skills: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    values = tuple(sorted({str(value).strip() for value in required_skills if str(value).strip()}))
    if not values:
        raise ChildRuntimeConfigError("managed child preparation requires at least one skill")
    return values


def _validate_explicit_runtime(
    repo: Path,
    required: tuple[str, ...],
    home_value: Path,
    manifest_value: Path,
) -> PreparedChildRuntime:
    home = home_value.expanduser().resolve()
    parent_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()
    if home == parent_home:
        raise ChildRuntimeConfigError(
            "prepared child home must differ from the parent CODEX_HOME"
        )
    if is_relative_to(home, repo):
        raise ChildRuntimeConfigError(
            f"prepared child home must stay outside the target repository: {home}"
        )
    manifest = load_child_closure_manifest(home, manifest_value)
    _assert_manifest_satisfies(required, manifest)
    return PreparedChildRuntime(
        home=home,
        manifest=manifest.path,
        source="explicit",
        cache_key=manifest.sha256,
        reused=True,
    )


def _resolve_standalone_sources(
    parent_home: Path,
    repo: Path,
    required: tuple[str, ...],
) -> tuple[tuple[str, Path], ...]:
    resolved: list[tuple[str, Path]] = []
    for skill_id in required:
        registry_entry = parent_home / "skills" / skill_id
        if not registry_entry.exists():
            if ":" in skill_id:
                continue
            raise ChildRuntimeConfigError(f"missing required skill source: {skill_id}")
        source = registry_entry.resolve()
        if not source.is_dir():
            raise ChildRuntimeConfigError(f"required skill source is not a directory: {skill_id}")
        if is_relative_to(source, repo):
            raise ChildRuntimeConfigError(
                f"required skill source is inside the target repository: {skill_id}"
            )
        for item in source.rglob("*"):
            if item.is_symlink():
                raise ChildRuntimeConfigError(
                    f"required skill source contains an internal symlink: {skill_id}"
                )
        resolved.append((skill_id, source))
    return tuple(resolved)


def _classify_inventory(
    home: Path,
    sources: tuple[tuple[str, Path], ...],
    records: tuple[ChildSkillInventoryRecord, ...],
) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    copied = {skill_id: home / "skills" / skill_id for skill_id, _ in sources}
    plugin_groups: dict[str, dict[str, object]] = {}
    external: list[str] = []
    for record in records:
        copied_root = copied.get(record.skill_id)
        if copied_root is not None:
            if not is_relative_to(record.path, copied_root):
                raise ChildAttestationError(
                    f"managed child skill path ownership mismatch: {record.skill_id}"
                )
            continue
        if is_relative_to(record.path, home):
            relative = record.path.relative_to(home)
            parts = relative.parts
            if len(parts) < 6 or parts[:2] != ("plugins", "cache"):
                raise ChildAttestationError(
                    f"enabled child skill has no deterministic owner: {record.skill_id}"
                )
            root_relative = Path(*parts[:5])
            root = home / root_relative
            if not root.is_dir():
                raise ChildAttestationError(
                    f"plugin payload root is missing for enabled skill: {record.skill_id}"
                )
            key = root_relative.as_posix()
            group = plugin_groups.setdefault(
                key,
                {
                    "id": "/".join(parts[2:5]),
                    "path": key,
                    "sha256": path_tree_hash(root),
                    "skill_ids": [],
                },
            )
            group["skill_ids"].append(record.skill_id)  # type: ignore[union-attr]
            continue
        external.append(record.skill_id)
    plugins = []
    for key in sorted(plugin_groups):
        group = plugin_groups[key]
        group["skill_ids"] = sorted(group["skill_ids"])  # type: ignore[arg-type]
        plugins.append(group)
    return plugins, tuple(sorted(external))


def _assert_required_present(
    required: tuple[str, ...], records: tuple[ChildSkillInventoryRecord, ...]
) -> None:
    loaded = {record.skill_id for record in records}
    missing = []
    for skill_id in required:
        if skill_id in loaded:
            continue
        suffix_matches = [item for item in loaded if item.rsplit(":", 1)[-1] == skill_id]
        if len(suffix_matches) != 1:
            missing.append(skill_id)
    if missing:
        raise ChildAttestationError(
            f"managed child inventory is missing required skills: {', '.join(missing)}"
        )


def _assert_manifest_satisfies(
    required: tuple[str, ...], manifest: ChildClosureManifest
) -> None:
    records = tuple(
        ChildSkillInventoryRecord(skill_id=skill_id, path=manifest.path)
        for skill_id in (
            *manifest.skill_ids,
            *manifest.plugin_skill_ids,
            *manifest.external_skill_ids,
        )
    )
    _assert_required_present(required, records)


def _remove_volatile_children(home: Path) -> None:
    retained = {"config.toml", "auth.json", "skills", "plugins"}
    for child in tuple(home.iterdir()):
        if child.name in retained:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _thread_lock(cache_key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(cache_key, threading.Lock())


def _publish_runtime(
    repo_root: Path,
    stage: Path,
    cache_key: str,
    required: tuple[str, ...],
) -> PreparedChildRuntime:
    destination = repo_root / cache_key
    locks = repo_root / ".locks"
    ensure_private_directory(locks)
    lock_path = locks / f"{cache_key}.lock"
    with _thread_lock(cache_key):
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if destination.exists():
                manifest = load_child_closure_manifest(destination, destination / MANIFEST_NAME)
                _assert_manifest_satisfies(required, manifest)
                return PreparedChildRuntime(
                    destination, manifest.path, "managed", cache_key, True
                )
            stage.replace(destination)
            manifest = load_child_closure_manifest(destination, destination / MANIFEST_NAME)
            _assert_manifest_satisfies(required, manifest)
            return PreparedChildRuntime(
                destination, manifest.path, "managed", cache_key, False
            )
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
