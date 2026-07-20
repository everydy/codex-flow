from pathlib import Path

import pytest

from codex_flow.execution_policy import (
    ExecutionMode,
    ExecutionProfile,
    FailureKind,
    FailureRecord,
    UnitGate,
    VerificationPolicyError,
    classify_execution_policy,
    retry_eligible,
    resolve_verification_spec,
)


def unit(*paths: str, title: str = "Update files", declared: str | None = None) -> dict:
    value = {"title": title, "allowed_paths": list(paths)}
    if declared is not None:
        value["execution_policy"] = {"declared_profile": declared}
    return value


def test_missing_profile_defaults_to_contract():
    policy = classify_execution_policy(unit("docs/guide.md"))

    assert policy.effective_profile is ExecutionProfile.CONTRACT
    assert policy.execution_mode is ExecutionMode.ISOLATED_CHILD
    assert policy.unit_gate is UnitGate.CONTRACT
    assert "missing" in " ".join(policy.inference_reasons)


def test_docs_only_requires_an_explicit_declaration_and_docs_absence_oracle():
    policy = classify_execution_policy(unit("README.md", "docs/guide.mdx", declared="docs_only"))

    assert policy.effective_profile is ExecutionProfile.DOCS_ONLY
    assert policy.execution_mode is ExecutionMode.PARENT_DIRECT
    assert policy.unit_gate is UnitGate.SMOKE


def test_docs_only_rejects_broad_docs_glob_that_could_include_code_or_config():
    policy = classify_execution_policy(unit("docs/**", declared="docs_only"))

    assert policy.effective_profile is ExecutionProfile.CONTRACT


@pytest.mark.parametrize("path", ["src/app.py", "pyproject.toml", "package-lock.json", "scripts/check.sh", "dist/app.js"])
def test_docs_only_is_raised_to_contract_when_any_path_is_not_provably_documentation(path):
    policy = classify_execution_policy(unit("docs/guide.md", path, declared="docs_only"))

    assert policy.effective_profile is ExecutionProfile.CONTRACT


@pytest.mark.parametrize(
    ("title", "path"),
    [
        ("Change authentication flow", "docs/auth.md"),
        ("Security hardening", "src/security.py"),
        ("Schema migration", "migrations/001.sql"),
        ("Native UI update", "ios/App.swift"),
        ("Deploy service", "docs/runbook.md"),
        ("External publish", "README.md"),
        ("Update application view", "Sources/AppView.swift"),
        ("Update OAuth login", "config/settings.yaml"),
    ],
)
def test_high_risk_inference_cannot_be_lowered_by_metadata(title, path):
    policy = classify_execution_policy(unit(path, title=title, declared="docs_only"))

    assert policy.effective_profile is ExecutionProfile.HIGH_RISK
    assert policy.execution_mode is ExecutionMode.ISOLATED_CHILD
    assert policy.unit_gate is UnitGate.FULL


def test_unknown_profile_fails_safe_to_contract():
    policy = classify_execution_policy(unit("README.md", declared="fast"))

    assert policy.effective_profile is ExecutionProfile.CONTRACT
    assert policy.declared_profile is None


def test_persisted_effective_profile_is_a_non_lowerable_safety_floor():
    policy = classify_execution_policy(
        {
            "title": "Update settings",
            "allowed_paths": ["config/settings.yaml"],
            "execution_policy": {"effective_profile": "high_risk"},
        }
    )

    assert policy.effective_profile is ExecutionProfile.HIGH_RISK


def test_failure_retry_matrix_and_duplicate_fingerprint():
    retryable = FailureRecord.create(
        kind=FailureKind.TEST_FINDING,
        phase="verify",
        signature="FAILED tests/test_api.py::test_contract at /tmp/run-123",
        attempt_id="attempt-1",
        expected_head="abc",
        observed_head="abc",
        scoped_diff_digest="diff",
    )
    non_retryable = FailureRecord.create(
        kind=FailureKind.HEAD_DRIFT,
        phase="adopt",
        signature="head changed",
        attempt_id="attempt-2",
        expected_head="abc",
        observed_head="def",
        scoped_diff_digest="diff",
    )

    assert retry_eligible(retryable, prior_fingerprints=set(), retries_used=0)
    assert not retry_eligible(retryable, prior_fingerprints={retryable.fingerprint}, retries_used=0)
    assert not retry_eligible(retryable, prior_fingerprints=set(), retries_used=1)
    assert not retry_eligible(non_retryable, prior_fingerprints=set(), retries_used=0)


def test_unfixable_test_finding_is_not_retryable():
    record = FailureRecord.create(
        kind=FailureKind.TEST_FINDING,
        phase="verify",
        signature="requires operator decision",
        attempt_id="attempt-1",
        expected_head="abc",
        observed_head="abc",
        scoped_diff_digest="diff",
        fixable=False,
    )

    assert not record.retryable
    assert not retry_eligible(record, prior_fingerprints=set(), retries_used=0)


def test_failure_fingerprint_ignores_attempt_nonce_timestamp_and_absolute_temp_path():
    first = FailureRecord.create(
        kind=FailureKind.REVIEW_FINDING,
        phase="review",
        signature="finding in /tmp/codex-a/output.txt at 2026-07-20T01:02:03Z",
        attempt_id="attempt-a",
        expected_head="abc",
        observed_head="abc",
        scoped_diff_digest="same",
    )
    second = FailureRecord.create(
        kind=FailureKind.REVIEW_FINDING,
        phase="review",
        signature="finding in /tmp/codex-b/output.txt at 2026-07-21T04:05:06Z",
        attempt_id="attempt-b",
        expected_head="abc",
        observed_head="abc",
        scoped_diff_digest="same",
    )

    assert first.fingerprint == second.fingerprint


def test_failure_fingerprint_normalizes_platform_temp_paths_and_offset_timestamps():
    first = FailureRecord.create(
        kind=FailureKind.TRANSIENT,
        phase="execute",
        signature="failed at /private/var/folders/a/run/output.log on 2026-07-20T01:02:03+09:00",
        attempt_id="attempt-a",
        expected_head="abc",
        observed_head="abc",
        scoped_diff_digest="same",
    )
    second = FailureRecord.create(
        kind=FailureKind.TRANSIENT,
        phase="execute",
        signature="failed at /private/var/folders/b/run/output.log on 2026-07-21T04:05:06+09:00",
        attempt_id="attempt-b",
        expected_head="abc",
        observed_head="abc",
        scoped_diff_digest="same",
    )

    assert first.fingerprint == second.fingerprint


def test_verification_resolves_only_repo_owned_allowlist_entries(tmp_path):
    spec = resolve_verification_spec(
        "unit-tests",
        execution_repo=tmp_path,
        declared_artifact_paths=(".pytest_cache/**",),
        network_denial_supported=True,
        allowlist={
            "unit-tests": {
                "argv": ("python3", "-m", "pytest", "tests/test_unit.py", "-q"),
                "timeout_seconds": 120,
                "network": "denied",
                "mutation_paths": (".pytest_cache/**",),
            }
        },
    )

    assert spec.argv == ("python3", "-m", "pytest", "tests/test_unit.py", "-q")
    assert spec.cwd == tmp_path.resolve()
    assert spec.shell is False
    assert spec.network == "denied"


@pytest.mark.parametrize(
    "entry",
    [
        {"argv": ("sh", "-c", "pytest; curl example.com")},
        {"argv": ("/tmp/evil/python3", "-m", "pytest")},
        {"argv": ("python3", "-m", "pip", "install", "pytest")},
        {"argv": ("curl", "https://example.com")},
        {"argv": ("python3", "-m", "pytest"), "cwd": "../escape"},
        {"argv": ("python3", "-m", "pytest"), "mutation_paths": ("src/**",)},
    ],
)
def test_verification_rejects_shell_arbitrary_executable_cwd_dependency_and_mutation_escape(tmp_path, entry):
    with pytest.raises(VerificationPolicyError):
        resolve_verification_spec(
            "unsafe",
            execution_repo=tmp_path,
            declared_artifact_paths=(".pytest_cache/**",),
            network_denial_supported=True,
            allowlist={"unsafe": entry},
        )


def test_verification_fails_closed_when_network_denial_is_unsupported(tmp_path):
    with pytest.raises(VerificationPolicyError, match="network"):
        resolve_verification_spec(
            "tests",
            execution_repo=tmp_path,
            declared_artifact_paths=(),
            network_denial_supported=False,
            allowlist={"tests": {"argv": ("python3", "-m", "pytest"), "network": "denied"}},
        )


def test_verification_rejects_declared_artifact_symlink_that_escapes_repo(tmp_path):
    outside = tmp_path.parent / "outside-artifacts"
    outside.mkdir(exist_ok=True)
    (tmp_path / "artifacts").symlink_to(outside, target_is_directory=True)

    with pytest.raises(VerificationPolicyError, match="mutation"):
        resolve_verification_spec(
            "tests",
            execution_repo=tmp_path,
            declared_artifact_paths=("artifacts/**",),
            network_denial_supported=True,
            allowlist={"tests": {"argv": ("python3", "-m", "pytest"), "mutation_paths": ("artifacts/**",)}},
        )


def test_plan_text_is_not_an_allowlist_key(tmp_path):
    with pytest.raises(VerificationPolicyError, match="allowlist"):
        resolve_verification_spec(
            "python3 -m pytest; touch owned",
            execution_repo=tmp_path,
            declared_artifact_paths=(),
            network_denial_supported=True,
            allowlist={},
        )
