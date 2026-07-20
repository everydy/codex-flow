from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatch
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping, Sequence


POLICY_VERSION = "1"


class ExecutionProfile(StrEnum):
    DOCS_ONLY = "docs_only"
    CONTRACT = "contract"
    HIGH_RISK = "high_risk"


class ExecutionMode(StrEnum):
    PARENT_DIRECT = "parent_direct"
    ISOLATED_CHILD = "isolated_child"


class UnitGate(StrEnum):
    SMOKE = "smoke"
    CONTRACT = "contract"
    FULL = "full"


class ReviewPolicy(StrEnum):
    FINAL_ONLY = "final_only"
    PER_UNIT = "per_unit"


class RecoveryClass(StrEnum):
    REPAIRABLE_IN_SCOPE = "repairable_in_scope"
    REPAIRABLE_NEW_SCOPE = "repairable_new_scope"
    ENVIRONMENT = "environment"
    OPERATOR = "operator"
    PROTOCOL = "protocol"


class FailureKind(StrEnum):
    PROTOCOL = "protocol"
    AUTH_CONFIG = "auth_config"
    SCOPE_MISMATCH = "scope_mismatch"
    HEAD_DRIFT = "head_drift"
    TEST_FINDING = "test_finding"
    REVIEW_FINDING = "review_finding"
    TRANSIENT = "transient"
    PROCESS_FAILURE = "process_failure"
    UNKNOWN = "unknown"


_PROFILE_ORDER = {
    ExecutionProfile.DOCS_ONLY: 0,
    ExecutionProfile.CONTRACT: 1,
    ExecutionProfile.HIGH_RISK: 2,
}

_PROFILE_RUNTIME = {
    ExecutionProfile.DOCS_ONLY: (ExecutionMode.PARENT_DIRECT, UnitGate.SMOKE),
    ExecutionProfile.CONTRACT: (ExecutionMode.PARENT_DIRECT, UnitGate.CONTRACT),
    ExecutionProfile.HIGH_RISK: (ExecutionMode.ISOLATED_CHILD, UnitGate.FULL),
}

_DOC_EXTENSIONS = {".md", ".mdx", ".rst", ".adoc", ".txt"}
_DOC_BASENAMES = {
    "readme",
    "changelog",
    "contributing",
    "code_of_conduct",
    "license",
    "notice",
    "authors",
}
_DOC_DIRECTORIES = {"doc", "docs", "documentation"}
_HIGH_RISK_TEXT = re.compile(
    r"\b(auth(?:entication|orization)?|oauth|oidc|sso|credentials?|secrets?|security|"
    r"schema\s+(?:migration|change)|database\s+migration|native\s+ui|"
    r"external\s+publish|publish(?:ing)?|deploy(?:ment)?)\b",
    flags=re.IGNORECASE,
)
_HIGH_RISK_PATH = re.compile(
    r"(^|/)(migrations?|security|auth|ios|android|native|deploy(?:ment)?|\.github/workflows)(/|$)|"
    r"\.(?:swift|kt|kts|m|mm|pbxproj)$",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class ExecutionPolicy:
    declared_profile: ExecutionProfile | None
    effective_profile: ExecutionProfile
    execution_mode: ExecutionMode
    unit_gate: UnitGate
    review_policy: ReviewPolicy
    inference_reasons: tuple[str, ...]
    policy_version: str = POLICY_VERSION

    def to_dict(self) -> dict:
        return {
            "policy_version": self.policy_version,
            "declared_profile": self.declared_profile.value if self.declared_profile else None,
            "effective_profile": self.effective_profile.value,
            "execution_mode": self.execution_mode.value,
            "unit_gate": self.unit_gate.value,
            "executor_adapter": "main" if self.execution_mode is ExecutionMode.PARENT_DIRECT else "isolated-child",
            "review_policy": self.review_policy.value,
            "automatic_repair": False,
            "inference_reasons": list(self.inference_reasons),
        }


def classify_execution_policy(unit: Mapping) -> ExecutionPolicy:
    raw_policy = unit.get("execution_policy")
    policy_data = raw_policy if isinstance(raw_policy, Mapping) else {}
    raw_declared = policy_data.get("declared_profile")
    reasons: list[str] = []
    try:
        declared = ExecutionProfile(str(raw_declared)) if raw_declared is not None else None
    except ValueError:
        declared = None
        reasons.append(f"unknown declared profile {raw_declared!r}; defaulted to contract")

    if declared is None:
        declared_or_default = ExecutionProfile.CONTRACT
        if not reasons:
            reasons.append("missing declared profile; defaulted to contract")
    else:
        declared_or_default = declared
        reasons.append(f"declared profile: {declared.value}")

    inferred, inferred_reason = infer_profile_lower_bound(unit)
    reasons.append(inferred_reason)
    try:
        persisted_floor = ExecutionProfile(str(policy_data.get("effective_profile")))
    except ValueError:
        persisted_floor = ExecutionProfile.CONTRACT
        reasons.append("unknown persisted effective profile; defaulted safety floor to contract")
    if "effective_profile" not in policy_data:
        persisted_floor = ExecutionProfile.DOCS_ONLY
    elif persisted_floor is not ExecutionProfile.DOCS_ONLY:
        reasons.append(f"persisted effective profile safety floor: {persisted_floor.value}")
    effective = max((declared_or_default, inferred, persisted_floor), key=_PROFILE_ORDER.__getitem__)
    if effective is not declared_or_default:
        reasons.append(f"raised profile from {declared_or_default.value} to {effective.value}")
    mode, gate = _PROFILE_RUNTIME[effective]
    requested_adapter = str(policy_data.get("executor_adapter") or "").replace("_", "-")
    requested_review = str(policy_data.get("review_policy") or "")
    if requested_adapter == "isolated-child":
        mode = ExecutionMode.ISOLATED_CHILD
        reasons.append("explicit isolated-child adapter")
    if effective is ExecutionProfile.HIGH_RISK:
        review_policy = ReviewPolicy.PER_UNIT
    elif requested_review == ReviewPolicy.PER_UNIT.value:
        review_policy = ReviewPolicy.PER_UNIT
        reasons.append("explicit per-unit review")
    else:
        review_policy = ReviewPolicy.FINAL_ONLY
    return ExecutionPolicy(declared, effective, mode, gate, review_policy, tuple(reasons))


def infer_profile_lower_bound(unit: Mapping) -> tuple[ExecutionProfile, str]:
    title = str(unit.get("title") or "")
    content = str(unit.get("content") or unit.get("excerpt") or "")
    paths = tuple(str(path) for path in unit.get("allowed_paths", ()) if isinstance(path, str))
    if paths and all(is_documentation_path(path) for path in paths):
        return ExecutionProfile.DOCS_ONLY, "documentation-only absence oracle passed before textual risk scan"
    if paths and all(is_test_scope_path(path) for path in paths):
        return ExecutionProfile.CONTRACT, "test-only absence oracle passed before textual risk scan"
    searchable = " ".join((title, content, *paths))
    if _HIGH_RISK_TEXT.search(searchable) or any(_HIGH_RISK_PATH.search(path) for path in paths):
        return ExecutionProfile.HIGH_RISK, "high-risk auth/security/migration/native/publish/deploy signal"
    return ExecutionProfile.CONTRACT, "documentation-only absence oracle did not prove safety"


def is_documentation_path(value: str) -> bool:
    path = value.replace("\\", "/").strip().lstrip("./")
    if not path:
        return False
    parts = tuple(part for part in path.split("/") if part)
    if not parts:
        return False
    if any(token in path for token in ("*", "?", "[", "]")):
        return False
    leaf = Path(parts[-1])
    stem = leaf.stem.lower()
    if leaf.suffix.lower() not in _DOC_EXTENSIONS and stem not in _DOC_BASENAMES:
        return False
    return len(parts) == 1 or parts[0].lower() in _DOC_DIRECTORIES


def is_test_scope_path(value: str) -> bool:
    normalized = value.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/"):
        return False
    parts = tuple(part for part in normalized.split("/") if part and part != ".")
    if not parts or ".." in parts:
        return False
    return parts[0].lower() in {"test", "tests"}


@dataclass(frozen=True)
class FailureRecord:
    kind: FailureKind
    phase: str
    fingerprint: str
    retryable: bool
    attempt_id: str
    expected_head: str
    observed_head: str
    recovery_class: RecoveryClass
    repair_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "phase": self.phase,
            "fingerprint": self.fingerprint,
            "retryable": self.retryable,
            "attempt_id": self.attempt_id,
            "expected_head": self.expected_head,
            "observed_head": self.observed_head,
            "recovery_class": self.recovery_class.value,
            "repair_paths_sha256": hashlib.sha256("\0".join(self.repair_paths).encode("utf-8")).hexdigest(),
            "repair_path_count": len(self.repair_paths),
        }

    @classmethod
    def create(
        cls,
        *,
        kind: FailureKind,
        phase: str,
        signature: str,
        attempt_id: str,
        expected_head: str,
        observed_head: str,
        scoped_diff_digest: str,
        policy_version: str = POLICY_VERSION,
        fixable: bool = True,
        repair_paths: Sequence[str] = (),
    ) -> "FailureRecord":
        normalized = normalize_failure_signature(signature)
        canonical = {
            "kind": kind.value,
            "phase": phase.strip().lower(),
            "signature": normalized,
            "expected_head": expected_head,
            "scoped_diff_digest": scoped_diff_digest,
            "policy_version": policy_version,
        }
        fingerprint = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        recovery_class = recovery_class_for(kind)
        return cls(
            kind=kind,
            phase=phase,
            fingerprint=fingerprint,
            retryable=fixable and recovery_class is RecoveryClass.REPAIRABLE_IN_SCOPE,
            attempt_id=attempt_id,
            expected_head=expected_head,
            observed_head=observed_head,
            recovery_class=recovery_class,
            repair_paths=tuple(str(path) for path in repair_paths),
        )


def recovery_class_for(kind: FailureKind) -> RecoveryClass:
    if kind in {FailureKind.TEST_FINDING, FailureKind.REVIEW_FINDING}:
        return RecoveryClass.REPAIRABLE_IN_SCOPE
    if kind is FailureKind.SCOPE_MISMATCH:
        return RecoveryClass.REPAIRABLE_NEW_SCOPE
    if kind in {FailureKind.TRANSIENT, FailureKind.PROCESS_FAILURE}:
        return RecoveryClass.ENVIRONMENT
    if kind is FailureKind.AUTH_CONFIG:
        return RecoveryClass.OPERATOR
    return RecoveryClass.PROTOCOL


def normalize_failure_signature(value: str) -> str:
    normalized = re.sub(
        r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b",
        "<timestamp>",
        value,
    )
    normalized = re.sub(r"(?<!\w)(?:/[^\s]+|[A-Za-z]:\\[^\s]+)", "<absolute-path>", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    return normalized


def retry_eligible(
    record: FailureRecord,
    *,
    prior_fingerprints: set[str],
    retries_used: int,
    approved_by_recovery_owner: bool = False,
    allowed_paths: Sequence[str] = (),
) -> bool:
    return (
        approved_by_recovery_owner
        and record.retryable
        and bool(record.repair_paths)
        and all(any(fnmatch(path, pattern) for pattern in allowed_paths) for path in record.repair_paths)
        and record.fingerprint not in prior_fingerprints
        and retries_used < 1
    )


class VerificationPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class VerificationSpec:
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: int
    network: str
    mutation_paths: tuple[str, ...]
    shell: bool = False


_SAFE_EXECUTABLES = {"python", "python3", "pytest", "ruff", "mypy", "npm", "pnpm", "yarn"}
_SHELL_METACHARACTERS = re.compile(r"[;&|`$<>\n\r]")


def resolve_verification_spec(
    key: str,
    *,
    execution_repo: str | Path,
    declared_artifact_paths: Sequence[str],
    network_denial_supported: bool,
    allowlist: Mapping[str, Mapping],
) -> VerificationSpec:
    if key not in allowlist:
        raise VerificationPolicyError(f"verification key is not in the repo-owned allowlist: {key!r}")
    entry = allowlist[key]
    argv = tuple(str(arg) for arg in entry.get("argv", ()))
    if not argv or argv[0] not in _SAFE_EXECUTABLES:
        raise VerificationPolicyError("verification executable is not allowed")
    if any(_SHELL_METACHARACTERS.search(arg) for arg in argv):
        raise VerificationPolicyError("shell syntax is not allowed in verification argv")
    lowered = tuple(arg.lower() for arg in argv)
    if "install" in lowered or "add" in lowered and Path(argv[0]).name in {"npm", "pnpm", "yarn"}:
        raise VerificationPolicyError("dependency installation is not allowed during verification")

    repo = Path(execution_repo).resolve()
    cwd_value = entry.get("cwd", ".")
    cwd = (repo / str(cwd_value)).resolve()
    if cwd != repo:
        raise VerificationPolicyError("verification cwd must be the execution repo")

    network = str(entry.get("network", "denied"))
    if network != "denied" or not network_denial_supported:
        raise VerificationPolicyError("verification requires a network-denied-capable runner")

    mutation_paths = tuple(str(path) for path in entry.get("mutation_paths", ()))
    for mutation in mutation_paths:
        _assert_mutation_path_inside_repo(repo, mutation)
        if not any(_path_pattern_within(mutation, declared) for declared in declared_artifact_paths):
            raise VerificationPolicyError(f"undeclared verification mutation path: {mutation}")

    timeout_seconds = int(entry.get("timeout_seconds", 300))
    if timeout_seconds <= 0:
        raise VerificationPolicyError("verification timeout must be positive")
    return VerificationSpec(argv, cwd, timeout_seconds, network, mutation_paths)


def _path_pattern_within(candidate: str, declared: str) -> bool:
    return candidate == declared or fnmatch(candidate, declared) or fnmatch(candidate.rstrip("/**"), declared)


def _assert_mutation_path_inside_repo(repo: Path, pattern: str) -> None:
    if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
        raise VerificationPolicyError(f"verification mutation path escapes execution repo: {pattern}")
    concrete_prefix = re.split(r"[*?[]", pattern, maxsplit=1)[0].rstrip("/") or "."
    resolved = (repo / concrete_prefix).resolve()
    try:
        resolved.relative_to(repo)
    except ValueError as exc:
        raise VerificationPolicyError(f"verification mutation path escapes execution repo: {pattern}") from exc
