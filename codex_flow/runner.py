from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path

from . import plan_readiness, plans, source_plan, state
from .attempt_ledger import AttemptLedger, LedgerConflict
from .execution_policy import ExecutionMode, FailureKind, FailureRecord, ReviewPolicy, classify_execution_policy, retry_eligible
from .main_unit import begin_main_unit
from .codex_cli import (
    ChildAttestationError,
    ChildRuntimeConfigError,
    CodexExecFailure,
    CodexExecTimeout,
    generate_child_attestation,
    verify_child_attestation,
)
from .child_runtime import REQUIRE_EXPLICIT_CHILD_ENV, ensure_prepared_child_runtime
from .cancellation import cancellation_requested
from .git_ops import (
    binary_patch_digest,
    candidate_diff_digest,
    changed_paths_between,
    changed_paths_since,
    commit_message as git_commit_message,
    commit_parent,
    commit_paths,
    commit_tree,
    dirty_paths,
    head_sha,
    head_summary,
    out_of_scope_diff_digest,
    prepare_branch,
    scoped_diff_digest,
    scoped_status_summary,
    stash_paths,
    status,
    worktree_patch_digest,
)
from .implementer_agent import CodexImplementerAgent, ImplementerAgentInput
from .reviewer_agent import (
    CommitUnitReview,
    CodexReadOnlyReviewer,
    ReviewGate,
    ReviewerAgentInput,
    ReviewerAgentResult,
    ReviewerProcessFailure,
    out_of_scope_paths,
    review_control_plane_exclusions,
    required_review_skills,
)


def next_ready_unit(queue_data: dict) -> dict | None:
    for unit in queue_data.get("units", []):
        if unit.get("status") == "ready":
            return unit
    return None


def unit_for_commit(queue_data: dict, commit_unit: plan_readiness.CommitUnit) -> dict:
    for unit in queue_data.get("units", []):
        if unit.get("id") == commit_unit.unit_id:
            return unit
    unit = {
        "id": commit_unit.unit_id,
        "number": commit_unit.number,
        "title": commit_unit.title,
        "status": "ready",
        "prompt_path": "",
        "updated_at": state.timestamp(),
    }
    queue_data.setdefault("units", []).append(unit)
    return unit


def render_prompt(queue_data: dict, unit: dict, plan_dir: Path, commit_unit: plan_readiness.CommitUnit | None = None) -> str:
    repo = plans.execution_repo_for_plan(plan_dir, queue_data)
    allowed = "\n".join(f"- {item}" for item in unit.get("allowed_paths", [])) or "- Not specified"
    verification = "\n".join(f"- {item}" for item in unit.get("verification", [])) or "- Not specified"
    skill_routing = render_skill_routing_prompt(unit, plan_dir, commit_unit)
    policy = unit.get("execution_policy") or {}
    per_unit_review = policy.get("review_policy") == ReviewPolicy.PER_UNIT.value
    plan_path = plan_dir / "plan.md"
    plan_content = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
    source_plan_content = read_optional(plan_dir / "source-plan.md")
    macro_plan_content = read_optional(plan_dir / "macro-plan.md")
    ticket_id = str(unit.get("ticket_id") or "")
    ticket_content = read_optional(plan_dir / "tickets" / f"{ticket_id}.md") if ticket_id else ""
    selected = ""
    if commit_unit:
        selected = "\n".join(["## Selected Commit Unit", "", f"### Commit {commit_unit.number}: {commit_unit.title}", "", commit_unit.content, ""])
    return "\n".join(
        [
            f"# Codex Flow Implementer Prompt: {unit['id']}",
            "",
            "## Mission",
            "",
            f"- Ticket: {queue_data.get('ticket_title')}",
            f"- Plan directory: {plan_dir}",
            f"- Unit: {unit['id']} - {unit['title']}",
            "",
            "## Allowed Paths",
            "",
            allowed,
            "",
            "## Verification",
            "",
            verification,
            "",
            "## Skill Routing Manifest",
            "",
            skill_routing,
            "",
            "Before implementation, load and apply every required skill named above. Required skills are mandatory and were attested before launch; if any cannot be loaded, stop without editing and return needs_work.",
            "Optional skills may be skipped only with an explicit reason in the final summary.",
            "",
            "## Unit Boundary Gates",
            "",
            "- Do not create or merge a real remote PR inside this commit-unit implementation; finalization commands handle PR and merge after all units are ready.",
            "- Do not deploy.",
            "- Do not reset, checkout, or revert unrelated user changes.",
            "- If secrets, accounts, payments, or external posting are required, stop after preparing the draft.",
            f"- Effective execution profile: {policy.get('effective_profile', 'contract')}",
            f"- Selected execution mode: {policy.get('execution_mode', 'isolated_child')}",
            f"- Selected unit gate: {policy.get('unit_gate', 'contract')}",
            "",
            "## Execution Contract",
            "",
            "Implement only this unit. Keep the diff narrow. Do not create a git commit; Codex Flow will commit after review.",
            (
                "A separate fresh read-only reviewer runs after implementation."
                if per_unit_review
                else "A deterministic scope/HEAD gate runs now; cumulative review runs once after all units."
            ),
            (
                "Blocker or important findings are held for a later main-owned recovery decision."
                if per_unit_review
                else "The unit gate never edits product files or automatically retries the unit."
            ),
            "",
            "Return one final line in one of these forms:",
            f'COMMIT_UNIT_READY title="{unit["title"]}" summary="..."',
            'COMMIT_UNIT_NEEDS_WORK reason="..."',
            "",
            "## Context",
            "",
            f"- Branch: {queue_data.get('branch', '-')}",
            f"- Previous commit: {head_summary(repo) if (repo / '.git').exists() else 'None'}",
            "",
            "## Full Plan Context",
            "",
            "Read this plan as the source of truth for the selected commit unit. Do not rely only on the generic unit title.",
            "",
            "```md",
            plan_content.strip() or "No plan.md content found.",
            "```",
            "",
            "## Source Plan Snapshot",
            "",
            "This is the original plan-first document adopted by route. Treat it as the upstream source.",
            "",
            "```md",
            source_plan_content.strip() or "No source-plan.md content found.",
            "```",
            "",
            "## Macro Plan Context",
            "",
            "```md",
            macro_plan_content.strip() or "No macro-plan.md content found.",
            "```",
            "",
            "## Selected Ticket",
            "",
            "```md",
            ticket_content.strip() or "No plan-local ticket found for this unit.",
            "```",
            "",
            selected,
        ]
    )


def read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def render_skill_routing_prompt(unit: dict, plan_dir: Path, commit_unit: plan_readiness.CommitUnit | None) -> str:
    entry = None
    plan_path = plan_dir / "plan.md"
    if commit_unit and plan_path.exists():
        entry = plan_readiness.skill_routing_for_commit(plan_path.read_text(encoding="utf-8"), commit_unit.number)
    if entry:
        return plan_readiness.format_skill_routing_entry(entry)
    required = tuple(unit.get("required_skills", []))
    optional = tuple(unit.get("optional_skills", []))
    evidence = unit.get("skill_routing_evidence", "-")
    return "\n".join(
        [
            f"- Phase: {unit.get('id', '-')}: {unit.get('title', '-')}",
            f"- Required skills: {plan_readiness.format_skill_list(required)}",
            f"- Optional skills: {plan_readiness.format_skill_list(optional)}",
            f"- Evidence: {evidence}",
        ]
    )


def run_next(
    plan_path: str | Path,
    dry_run: bool = False,
    execute: bool = False,
    commit: bool = False,
    codex_command: str = "codex",
    codex_args: list[str] | None = None,
    allow_dirty: bool = False,
    no_branch: bool = False,
    auto_resolve: bool = False,
    repair_attempts: int = 0,
    accept_source_drift: bool = False,
    codex_timeout_seconds: int | None = None,
) -> dict | None:
    plan_dir, queue_data = (
        plans.load_queue_read_only(plan_path) if execute else plans.load_queue(plan_path)
    )
    plan_content = (plan_dir / "plan.md").read_text(encoding="utf-8")
    commit_units = {item.unit_id: item for item in plan_readiness.parse_commit_units(plan_content)}
    prepared_runtime = None
    if execute:
        _, plan_content, log_content = plan_readiness.read_plan_file(plan_dir / "plan.md")
        readiness = plan_readiness.check_plan_ready(plan_content, log_content)
        if readiness.next_unit is None:
            return None
        commit_unit = readiness.next_unit
        unit = unit_for_commit(queue_data, commit_unit)
        if unit.get("status") == "human_gate":
            return {
                "unit": unit,
                "prompt_path": plan_dir / "prompts" / f"{unit['id']}.md",
                "action": "human_gate",
                "reason": "Unit is held at human_gate because the source plan needs manual split or approval.",
                "changed": False,
            }
    else:
        unit = next_ready_unit(queue_data)
        if unit is None:
            return None
        commit_unit = commit_units.get(unit["id"])

    prompt_path = plan_dir / "prompts" / f"{unit['id']}.md"
    drift = source_plan.check_source_drift(plan_dir)
    if drift.changed and not accept_source_drift:
        return {
            "unit": unit,
            "prompt_path": prompt_path,
            "action": "source_drift",
            "reason": drift.reason,
            "source_path": str(drift.source_path) if drift.source_path else "",
            "changed": False,
        }
    if execute:
        recovered = recover_isolated_release(
            plan_dir=plan_dir,
            queue_data=queue_data,
            unit=unit,
            prompt_path=prompt_path,
            commit_number=commit_unit.number,
        )
        if recovered is not None:
            return recovered
    if execute:
        policy = classify_execution_policy(unit)
        unit["execution_policy"] = policy.to_dict()
        require_explicit_child = os.environ.get(
            REQUIRE_EXPLICIT_CHILD_ENV, ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        if policy.execution_mode is ExecutionMode.ISOLATED_CHILD and require_explicit_child:
            selected_unit = commit_unit or commit_units.get(str(unit.get("id")))
            if selected_unit is None:
                raise ChildRuntimeConfigError("isolated preflight could not resolve the selected unit")
            skill_entry = plan_readiness.skill_routing_for_commit(plan_content, selected_unit.number)
            required_skills = required_review_skills(
                tuple(skill_entry.required_skills if skill_entry else unit.get("required_skills", []))
            )
            prepared_runtime = ensure_prepared_child_runtime(
                repo=plans.execution_repo_for_plan(plan_dir, queue_data),
                required_skills=required_skills,
                command=codex_command,
                extra_args=codex_args or [],
                timeout_seconds=codex_timeout_seconds,
            )
        plan_dir, queue_data = plans.load_queue(plan_dir / "plan.md")
        synced_unit = unit_for_commit(queue_data, commit_unit)
        if synced_unit.get("id") != unit.get("id"):
            raise ChildRuntimeConfigError("selected unit changed during isolated preflight")
        unit = synced_unit
        unit["execution_policy"] = policy.to_dict()
    prompt_text = render_prompt(queue_data, unit, plan_dir, commit_unit)
    if dry_run:
        return {"unit": unit, "prompt_path": prompt_path, "prompt": prompt_text, "changed": False}

    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt_text, encoding="utf-8")
    if execute:
        if policy.execution_mode is ExecutionMode.PARENT_DIRECT:
            contract = begin_main_unit(plan_dir / "plan.md", unit_id=unit["id"])
            append_log(plan_dir, f"Main handoff opened for {unit['id']} at ledger revision {contract.ledger_revision}.")
            return {
                "unit": unit,
                "prompt_path": prompt_path,
                "action": "main_handoff",
                "reason": "main agent owns implementation; complete or hold the open transaction",
                "contract": {
                    "owner": "main",
                    "unit_id": contract.unit_id,
                    "ledger_path": str(contract.ledger_path),
                    "ledger_revision": contract.ledger_revision,
                    "expected_head": contract.expected_head,
                    "allowed_paths": list(contract.allowed_paths),
                    "queue_revision": contract.queue_revision,
                },
            }
        return execute_unit(
            plan_dir=plan_dir,
            queue_data=queue_data,
            unit=unit,
            prompt_path=prompt_path,
            prompt_text=prompt_text,
            commit_unit=commit_unit,
            commit=commit,
            codex_command=codex_command,
            codex_args=codex_args or [],
            allow_dirty=allow_dirty,
            no_branch=no_branch,
            auto_resolve=auto_resolve,
            repair_attempts=repair_attempts,
            codex_timeout_seconds=codex_timeout_seconds,
            prepared_runtime=prepared_runtime,
        )

    unit["status"] = "prompted"
    unit["prompt_path"] = str(prompt_path.relative_to(plan_dir))
    unit["updated_at"] = state.timestamp()
    plans.save_queue(plan_dir, queue_data)
    append_log(plan_dir, f"Prompted commit unit {unit.get('number') or unit['id']}: {unit['title']} -> {unit['prompt_path']}")
    state.refresh_dashboard(plans.execution_context_for_plan(plan_dir, queue_data).source_repo)
    return {"unit": unit, "prompt_path": prompt_path, "prompt": prompt_text, "changed": True}


def execute_unit(
    plan_dir: Path,
    queue_data: dict,
    unit: dict,
    prompt_path: Path,
    prompt_text: str,
    commit_unit: plan_readiness.CommitUnit | None,
    commit: bool,
    codex_command: str,
    codex_args: list[str],
    allow_dirty: bool,
    no_branch: bool,
    auto_resolve: bool,
    repair_attempts: int,
    codex_timeout_seconds: int | None,
    prepared_runtime=None,
) -> dict:
    execution_context = plans.execution_context_for_plan(plan_dir, queue_data)
    if not no_branch:
        execution_context = plans.prepare_execution_branch(plan_dir, queue_data)
    repo = execution_context.execution_repo
    source_repo = execution_context.source_repo
    policy = classify_execution_policy(unit)
    branch = queue_data.get("branch") or f"codex/{queue_data.get('plan_slug', 'plan')}"
    selected_unit = commit_unit or plan_readiness.CommitUnit(number=unit.get("number") or int(str(unit["id"]).split("-")[-1]), title=unit["title"], content="")
    skill_entry = plan_readiness.skill_routing_for_commit(
        (plan_dir / "plan.md").read_text(encoding="utf-8"),
        selected_unit.number,
    )
    required_skills = required_review_skills(
        tuple(skill_entry.required_skills if skill_entry else unit.get("required_skills", []))
    )
    attested_plan = plan_dir / "source-plan.md"
    if not attested_plan.exists():
        attested_plan = plan_dir / "plan.md"
    try:
        runtime = prepared_runtime or ensure_prepared_child_runtime(
            repo=repo,
            required_skills=required_skills,
            command=codex_command,
            extra_args=codex_args,
            timeout_seconds=codex_timeout_seconds,
        )
        append_log(
            plan_dir,
            "child_runtime "
            f"event={'reuse' if runtime.reused else 'prepare'} "
            f"source={runtime.source} cache_key={runtime.cache_key}",
        )
        attestation = generate_child_attestation(
            repo=repo,
            plan_path=attested_plan,
            child_home=runtime.home,
            manifest_path=runtime.manifest,
            command=codex_command,
            extra_args=codex_args,
            timeout_seconds=codex_timeout_seconds,
        )
        verify_child_attestation(
            attestation,
            repo=repo,
            plan_path=attested_plan,
            child_home=runtime.home,
            manifest_path=runtime.manifest,
            required_skills=required_skills,
            command=codex_command,
            extra_args=codex_args,
            nonce_ledger=plan_dir / "attestations" / "used-nonces.json",
            timeout_seconds=codex_timeout_seconds,
        )
    except (ChildAttestationError, ChildRuntimeConfigError, OSError) as exc:
        unit["status"] = "needs_work"
        unit["updated_at"] = state.timestamp()
        unit["last_needs_work_reason"] = str(exc)
        unit["changed_paths"] = []
        plans.save_queue(plan_dir, queue_data)
        append_log(plan_dir, f"Commit unit {selected_unit.number} needs_work before edit: {exc}")
        append_log(plan_dir, f"child_runtime event=deny reason={type(exc).__name__}")
        state.refresh_dashboard(source_repo)
        return {
            "unit": unit,
            "prompt_path": prompt_path,
            "action": "needs_work",
            "reason": str(exc),
            "changed_paths": [],
            "auto_resolved_dirty": [],
            "repair_attempts": 0,
            "repair_reason": str(exc),
            "review_gate": None,
            "child_attestation": None,
        }
    attestation_path = plan_dir / "attestations" / unit["id"] / f"{attestation.nonce}.json"
    attestation_path.parent.mkdir(parents=True, exist_ok=True)
    attestation_path.write_text(json.dumps(attestation.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    unit["child_attestation"] = str(attestation_path.relative_to(plan_dir))
    resume_needs_work = unit.get("status") == "needs_work"
    resume_reason = str(unit.get("last_needs_work_reason") or unit.get("repair_reason") or "") if resume_needs_work else ""
    resume_changed_paths = (
        [str(path) for path in unit.get("changed_paths", []) if isinstance(path, str)]
        if resume_needs_work
        else []
    )
    initial_status = status(repo)
    dirty = dirty_paths(initial_status)
    auto_resolved_dirty: list[str] = []
    preserved_repair_dirty: list[str] = []
    if dirty and not allow_dirty:
        if execution_context.mode == "in_place":
            raise SystemExit(
                "in-place execution requires a clean repository; "
                "automatic stash is disabled: " + ", ".join(dirty)
            )
        if not auto_resolve:
            raise SystemExit(f"Working tree must be clean before execute, excluding .codex-flow: {', '.join(dirty)}")
        if resume_changed_paths:
            resume_path_set = set(resume_changed_paths)
            preserved_repair_dirty = [path for path in dirty if path in resume_path_set]
            dirty = [path for path in dirty if path not in resume_path_set]
            if preserved_repair_dirty:
                append_log(plan_dir, f"Preserved previous needs_work changes for {unit['id']}: {', '.join(preserved_repair_dirty)}")
        if not dirty:
            append_log(plan_dir, f"auto_resolved dirty_worktree for {unit['id']} by preserving previous needs_work changes.")
        else:
            message = f"codex-flow auto-shelve before {unit['id']} {state.timestamp()}"
            stash_paths(repo, dirty, message)
            auto_resolved_dirty = dirty
            append_log(plan_dir, f"auto_resolved dirty_worktree for {unit['id']} by stash: {', '.join(dirty)}")
    if not no_branch and execution_context.mode != "in_place":
        prepare_branch(repo, branch)
    before_head = head_summary(repo)
    before = status(repo)
    resume_state_digest = hashlib.sha256(
        "\0".join((before_head or "", scoped_diff_digest(repo, []), resume_reason)).encode("utf-8")
    ).hexdigest()
    if resume_needs_work and unit.get("last_failure_state_digest") == resume_state_digest:
        reason = "same canonical failure state already attempted; new evidence or a scoped repair is required"
        append_log(plan_dir, f"Commit unit {selected_unit.number} held without duplicate retry: {reason}")
        return {
            "unit": unit,
            "prompt_path": prompt_path,
            "action": "needs_work",
            "reason": reason,
            "changed_paths": list(unit.get("changed_paths", [])),
            "auto_resolved_dirty": auto_resolved_dirty,
            "repair_attempts": int(unit.get("repair_attempts") or 0),
            "review_gate": unit.get("review_gate"),
        }
    unit["status"] = "in_progress"
    unit["prompt_path"] = str(prompt_path.relative_to(plan_dir))
    unit["updated_at"] = state.timestamp()
    plans.save_queue(plan_dir, queue_data)
    append_log(plan_dir, f"Started commit unit {unit.get('number') or unit['id']}: {unit['title']} on {branch}.")

    agent = CodexImplementerAgent(
        command=codex_command,
        extra_args=codex_args,
        child_home=runtime.home,
    )
    reviewer = (
        CodexReadOnlyReviewer(
            command=codex_command,
            extra_args=codex_args,
            child_home=runtime.home,
        )
        if policy.review_policy is ReviewPolicy.PER_UNIT
        else None
    )
    implementation_result = None
    review_result = None
    last_repair_reason = resume_reason
    previous_repair_attempts = int(unit.get("repair_attempts") or 0) if resume_needs_work else 0
    used_repair_attempts = 0
    attempts = execution_attempts(repair_attempts, resume_needs_work=resume_needs_work, previous_repair_attempts=previous_repair_attempts)
    repair_attempt_limit = max(attempts)
    for index, attempt in enumerate(attempts):
        if attempt > 0:
            used_repair_attempts = attempt
            unit["status"] = "in_progress"
            unit["repair_attempts"] = attempt
            unit["last_needs_work_reason"] = last_repair_reason
            unit["updated_at"] = state.timestamp()
            plans.save_queue(plan_dir, queue_data)
            append_log(plan_dir, f"Repair attempt {attempt}/{repair_attempt_limit} for commit unit {selected_unit.number}: {last_repair_reason}")
        attempt_dir = execution_attempt_dir(plan_dir, unit["id"], attempt)
        attempt_ledger_path = attempt_dir / "attempt-ledger.json"
        cancel_observed = False
        def scoped_diff_probe() -> str:
            return scoped_diff_digest(repo, [str(path) for path in unit.get("allowed_paths", [])])
        def cancel_probe() -> bool:
            nonlocal cancel_observed
            requested = cancellation_requested(attempt_dir, unit_id=unit["id"], attempt=attempt)
            if requested and not cancel_observed:
                cancel_observed = True
                attempt_ledger.append_event(
                    {
                        "unit_id": unit["id"],
                        "phase": "operator",
                        "event": "operator_cancel_observed",
                        "ledger_revision": attempt_ledger.load().revision,
                        "attempt": attempt,
                        "reason": "preserve_changes",
                    }
                )
            return requested
        attempt_ledger = AttemptLedger(attempt_ledger_path)
        allowed_paths = [str(path) for path in unit.get("allowed_paths", [])]
        if attempt_ledger.load().revision == 0:
            attempt_ledger.start_attempt(
                expected_head=before_head or "",
                scoped_diff_digest=scoped_diff_probe(),
                full_diff_digest=scoped_diff_digest(repo, []),
                out_of_scope_diff_digest=out_of_scope_diff_digest(repo, allowed_paths),
                allowed_paths=allowed_paths,
            )
        else:
            try:
                attempt_ledger.validate_start(
                    expected_head=before_head or "",
                    scoped_diff_digest=scoped_diff_probe(),
                    full_diff_digest=scoped_diff_digest(repo, []),
                    out_of_scope_diff_digest=out_of_scope_diff_digest(repo, allowed_paths),
                    allowed_paths=allowed_paths,
                )
            except ValueError as exc:
                unit["status"] = "needs_work"
                unit["last_needs_work_reason"] = str(exc)
                unit["updated_at"] = state.timestamp()
                plans.save_queue(plan_dir, queue_data)
                append_log(plan_dir, f"Commit unit {selected_unit.number} stale attempt held: {exc}")
                return {
                    "unit": unit,
                    "prompt_path": prompt_path,
                    "action": "needs_work",
                    "reason": str(exc),
                    "changed_paths": list(unit.get("changed_paths", [])),
                    "auto_resolved_dirty": auto_resolved_dirty,
                    "repair_attempts": used_repair_attempts,
                    "review_gate": unit.get("review_gate"),
                }
        agent_input = ImplementerAgentInput(
            repo=repo,
            plan_path=plan_dir / "plan.md",
            plan_content=(plan_dir / "plan.md").read_text(encoding="utf-8"),
            unit=selected_unit,
            previous_commit=head_summary(repo),
            git_status=scoped_status_summary(status(repo), [str(path) for path in unit.get("allowed_paths", [])]),
            repair_attempt=attempt,
            repair_reason=last_repair_reason,
            execution_policy=dict(unit.get("execution_policy") or {}),
            allowed_paths=tuple(str(path) for path in unit.get("allowed_paths", [])),
        )
        try:
            implementation_result = agent.implement(
                agent_input,
                diagnostic_dir=attempt_dir,
                timeout_seconds=codex_timeout_seconds,
                attempt_ledger_path=attempt_ledger_path,
                diff_probe=scoped_diff_probe,
                cancel_probe=cancel_probe,
            )
        except (CodexExecTimeout, CodexExecFailure) as exc:
            partial_changed = repair_changed_paths(preserved_repair_dirty, before, status(repo))
            unit["status"] = "needs_work"
            unit["updated_at"] = state.timestamp()
            unit["repair_attempts"] = used_repair_attempts
            unit["last_needs_work_reason"] = str(exc)
            unit["diagnostic_path"] = str(exc.diagnostic_dir.relative_to(plan_dir))
            unit["changed_paths"] = partial_changed
            current_snapshot = attempt_ledger.load()
            ledger_snapshot = attempt_ledger.finalize_attempt(
                expected_revision=current_snapshot.revision,
                expected_head=before_head or "",
                observed_head=head_summary(repo) or "",
                scoped_diff_digest=scoped_diff_probe(),
                full_diff_digest=scoped_diff_digest(repo, []),
                out_of_scope_diff_digest=out_of_scope_diff_digest(repo, allowed_paths),
                process_closed=current_snapshot.record.get("descendants_remaining") is False,
                termination_reason=str(current_snapshot.record.get("status") or "process_failure"),
            )
            unit["attempt_ledger_revision"] = ledger_snapshot.revision
            failure = FailureRecord.create(
                kind=FailureKind.PROCESS_FAILURE,
                phase="implementation",
                signature=str(exc),
                attempt_id=f"{unit['id']}/attempt-{attempt}",
                expected_head=before_head or "",
                observed_head=head_summary(repo) or "",
                scoped_diff_digest=scoped_diff_probe(),
                fixable=False,
            )
            unit["failure_fingerprints"] = sorted({*unit.get("failure_fingerprints", []), failure.fingerprint})
            unit["last_failure_state_digest"] = hashlib.sha256(
                "\0".join((before_head or "", scoped_diff_digest(repo, []), str(exc))).encode("utf-8")
            ).hexdigest()
            write_attempt_handoff(
                attempt_dir,
                unit_id=unit["id"],
                ledger_revision=ledger_snapshot.revision,
                status="needs_work",
                next_action="inspect held diagnostics; do not retry the same fingerprint",
            )
            plans.save_queue(plan_dir, queue_data)
            append_log(
                plan_dir,
                f"Commit unit {selected_unit.number} needs_work: {exc} | ledger_revision={ledger_snapshot.revision}",
            )
            state.refresh_dashboard(source_repo)
            return {
                "unit": unit,
                "prompt_path": prompt_path,
                "action": "needs_work",
                "reason": str(exc),
                "diagnostic_path": exc.diagnostic_dir,
                "changed_paths": partial_changed,
                "auto_resolved_dirty": auto_resolved_dirty,
                "repair_attempts": used_repair_attempts,
                "repair_reason": str(exc),
                "review_gate": None,
            }
        review_expected_head = head_summary(repo) or ""
        review_exclusions = review_control_plane_exclusions(repo, attempt_dir, attempt_ledger_path)
        review_expected_digest = candidate_diff_digest(repo, review_exclusions)
        try:
            if reviewer is None:
                review_result = deterministic_unit_gate_result(
                    implementation_result.session_id,
                    unit,
                    review_expected_head,
                    review_expected_digest,
                )
            else:
                review_result = reviewer.review(
                ReviewerAgentInput(
                    repo=repo,
                    plan_path=plan_dir / "plan.md",
                    plan_content=(plan_dir / "plan.md").read_text(encoding="utf-8"),
                    unit=selected_unit,
                    implementation_session_id=implementation_result.session_id,
                    expected_head=review_expected_head,
                    expected_full_diff_digest=review_expected_digest,
                    excluded_candidate_paths=review_exclusions,
                    execution_policy=dict(unit.get("execution_policy") or {}),
                    allowed_paths=tuple(str(path) for path in unit.get("allowed_paths", [])),
                ),
                diagnostic_dir=attempt_dir,
                timeout_seconds=codex_timeout_seconds,
                attempt_ledger_path=attempt_ledger_path,
                diff_probe=scoped_diff_probe,
                cancel_probe=cancel_probe,
                )
        except ReviewerProcessFailure as exc:
            partial_changed = repair_changed_paths(preserved_repair_dirty, before, status(repo))
            unit["status"] = "needs_work"
            unit["updated_at"] = state.timestamp()
            unit["repair_attempts"] = used_repair_attempts
            unit["last_needs_work_reason"] = str(exc)
            unit["diagnostic_path"] = str(exc.diagnostic_dir.relative_to(plan_dir))
            unit["changed_paths"] = partial_changed
            current_snapshot = attempt_ledger.load()
            ledger_snapshot = attempt_ledger.finalize_attempt(
                expected_revision=current_snapshot.revision,
                expected_head=before_head or "",
                observed_head=head_summary(repo) or "",
                scoped_diff_digest=scoped_diff_probe(),
                full_diff_digest=scoped_diff_digest(repo, []),
                out_of_scope_diff_digest=out_of_scope_diff_digest(repo, allowed_paths),
                process_closed=current_snapshot.record.get("descendants_remaining") is False,
                termination_reason=str(current_snapshot.record.get("status") or "review_process_failure"),
            )
            unit["attempt_ledger_revision"] = ledger_snapshot.revision
            failure_kind = (
                FailureKind.PROTOCOL
                if not exc.head_unchanged or not exc.full_diff_digest_unchanged
                else FailureKind.PROCESS_FAILURE
            )
            failure = FailureRecord.create(
                kind=failure_kind,
                phase="review",
                signature=str(exc),
                attempt_id=f"{unit['id']}/attempt-{attempt}",
                expected_head=review_expected_head,
                observed_head=exc.observed_head,
                scoped_diff_digest=scoped_diff_probe(),
                fixable=False,
            )
            unit["failure_fingerprints"] = sorted({*unit.get("failure_fingerprints", []), failure.fingerprint})
            unit["last_failure_state_digest"] = hashlib.sha256(
                "\0".join((review_expected_head, scoped_diff_digest(repo, []), str(exc))).encode("utf-8")
            ).hexdigest()
            review_path = write_review_failure_attempt(
                plan_dir,
                unit["id"],
                attempt,
                exc,
                implementation_session_id=implementation_result.session_id,
                child_attestation=str(attestation_path.relative_to(plan_dir)),
                ledger_revision=ledger_snapshot.revision,
            )
            write_attempt_handoff(
                attempt_dir,
                unit_id=unit["id"],
                ledger_revision=ledger_snapshot.revision,
                status="needs_work",
                next_action="inspect held review diagnostics; do not retry the same fingerprint",
            )
            ensure_attempt_view_revision(
                ledger_snapshot.revision,
                queue_unit=unit,
                review_path=review_path,
                handoff_path=attempt_dir / "handoff.json",
            )
            plans.save_queue(plan_dir, queue_data)
            append_log(
                plan_dir,
                f"Commit unit {selected_unit.number} needs_work in review: {exc} | ledger_revision={ledger_snapshot.revision}",
            )
            state.refresh_dashboard(source_repo)
            return {
                "unit": unit,
                "prompt_path": prompt_path,
                "action": "needs_work",
                "reason": str(exc),
                "diagnostic_path": exc.diagnostic_dir,
                "changed_paths": partial_changed,
                "auto_resolved_dirty": auto_resolved_dirty,
                "repair_attempts": used_repair_attempts,
                "repair_reason": str(exc),
                "review_gate": None,
            }
        current_snapshot = attempt_ledger.load()
        current_changed = repair_changed_paths(preserved_repair_dirty, before, status(repo))
        ledger_snapshot = attempt_ledger.finalize_attempt(
            expected_revision=current_snapshot.revision,
            expected_head=before_head or "",
            observed_head=head_summary(repo) or "",
            scoped_diff_digest=scoped_diff_probe(),
            full_diff_digest=scoped_diff_digest(repo, []),
            out_of_scope_diff_digest=out_of_scope_diff_digest(repo, allowed_paths),
            process_closed=current_snapshot.record.get("descendants_remaining") is False,
            termination_reason="completed" if current_snapshot.record.get("status") == "pass" else str(current_snapshot.record.get("status")),
        )
        unit["attempt_ledger_revision"] = ledger_snapshot.revision
        review_path = write_review_attempt(
            plan_dir,
            unit["id"],
            attempt,
            review_result,
            child_attestation=str(attestation_path.relative_to(plan_dir)),
            ledger_revision=ledger_snapshot.revision,
        )
        write_attempt_handoff(
            attempt_dir,
            unit_id=unit["id"],
            ledger_revision=ledger_snapshot.revision,
            status=review_result.review.status,
            next_action="continue gate evaluation",
        )
        ensure_attempt_view_revision(
            ledger_snapshot.revision,
            queue_unit=unit,
            review_path=review_path,
            handoff_path=attempt_dir / "handoff.json",
        )
        if review_result.review.gate:
            unit["review_gate"] = review_result.review.gate.to_dict()
            append_log(plan_dir, f"Review gate for commit unit {selected_unit.number} attempt {attempt}: {review_gate_summary(review_result.review)}")
        if review_result.review.status != "needs_work":
            break
        last_repair_reason = review_result.review.reason
        failure = FailureRecord.create(
            kind=(
                FailureKind.PROTOCOL
                if review_result.review.failure_kind == "protocol_failure"
                else FailureKind.REVIEW_FINDING
            ),
            phase="review",
            signature=last_repair_reason,
            attempt_id=f"{unit['id']}/attempt-{attempt}",
            expected_head=before_head or "",
            observed_head=head_summary(repo) or "",
            scoped_diff_digest=scoped_diff_probe(),
            fixable=review_result.review.retryable,
        )
        prior_fingerprints = set(str(value) for value in unit.get("failure_fingerprints", []))
        can_retry = retry_eligible(
            failure,
            prior_fingerprints=prior_fingerprints,
            retries_used=used_repair_attempts,
        )
        unit["failure_fingerprints"] = sorted({*prior_fingerprints, failure.fingerprint})
        unit["last_failure_state_digest"] = hashlib.sha256(
            "\0".join((before_head or "", scoped_diff_digest(repo, []), last_repair_reason)).encode("utf-8")
        ).hexdigest()
        if not review_result.review.retryable:
            unit["status"] = "needs_work"
            unit["updated_at"] = state.timestamp()
            unit["repair_attempts"] = used_repair_attempts
            unit["last_needs_work_reason"] = last_repair_reason
            if review_result.review.gate:
                unit["review_gate"] = review_result.review.gate.to_dict()
            partial_changed = repair_changed_paths(preserved_repair_dirty, before, status(repo))
            unit["changed_paths"] = partial_changed
            plans.save_queue(plan_dir, queue_data)
            append_log(plan_dir, f"Commit unit {selected_unit.number} needs_work without retry: {last_repair_reason}")
            state.refresh_dashboard(source_repo)
            return {
                "unit": unit,
                "prompt_path": prompt_path,
                "action": "needs_work",
                "reason": last_repair_reason,
                "commit": "",
                "changed_paths": partial_changed,
                "auto_resolved_dirty": auto_resolved_dirty,
                "repair_attempts": used_repair_attempts,
                "repair_reason": last_repair_reason,
                "review_gate": review_gate_payload(review_result.review),
            }
        if can_retry and index < len(attempts) - 1:
            append_log(plan_dir, f"Commit unit {selected_unit.number} requested repair: {last_repair_reason}")
            continue
        unit["status"] = "needs_work"
        unit["updated_at"] = state.timestamp()
        unit["repair_attempts"] = used_repair_attempts
        unit["last_needs_work_reason"] = last_repair_reason
        if review_result.review.gate:
            unit["review_gate"] = review_result.review.gate.to_dict()
        partial_changed = repair_changed_paths(preserved_repair_dirty, before, status(repo))
        unit["changed_paths"] = partial_changed
        plans.save_queue(plan_dir, queue_data)
        append_log(plan_dir, f"Commit unit {selected_unit.number} needs_work: {last_repair_reason}")
        state.refresh_dashboard(source_repo)
        return {
            "unit": unit,
            "prompt_path": prompt_path,
            "action": "needs_work",
            "reason": last_repair_reason,
            "commit": "",
            "changed_paths": partial_changed,
            "auto_resolved_dirty": auto_resolved_dirty,
            "repair_attempts": used_repair_attempts,
            "repair_reason": last_repair_reason,
            "review_gate": review_gate_payload(review_result.review),
        }
    if implementation_result is None or review_result is None:
        raise SystemExit("Codex implementer or reviewer did not return a result")

    after = status(repo)
    changed = repair_changed_paths(preserved_repair_dirty, before, after)
    after_head = head_summary(repo)
    if after_head != before_head:
        reason = f"implementer moved HEAD before orchestrated unit commit: {before_head or 'None'} -> {after_head or 'None'}"
        unit["status"] = "needs_work"
        unit["updated_at"] = state.timestamp()
        unit["repair_attempts"] = used_repair_attempts
        unit["last_needs_work_reason"] = reason
        unit["changed_paths"] = changed
        if review_result.review.gate:
            unit["review_gate"] = review_result.review.gate.to_dict()
        plans.save_queue(plan_dir, queue_data)
        append_log(plan_dir, f"Commit unit {selected_unit.number} needs_work before commit: {reason}")
        state.refresh_dashboard(source_repo)
        return {
            "unit": unit,
            "prompt_path": prompt_path,
            "action": "needs_work",
            "reason": reason,
            "commit": "",
            "changed_paths": changed,
            "auto_resolved_dirty": auto_resolved_dirty,
            "repair_attempts": used_repair_attempts,
            "repair_reason": reason,
            "review_gate": review_gate_payload(review_result.review),
        }
    outside_scope = out_of_scope_paths(changed, [str(path) for path in unit.get("allowed_paths", [])])
    if outside_scope:
        reason = f"unit changed paths outside allowed scope: {', '.join(outside_scope)}"
        unit["status"] = "needs_work"
        unit["updated_at"] = state.timestamp()
        unit["repair_attempts"] = used_repair_attempts
        unit["last_needs_work_reason"] = reason
        unit["changed_paths"] = changed
        if review_result.review.gate:
            unit["review_gate"] = review_result.review.gate.to_dict()
        plans.save_queue(plan_dir, queue_data)
        append_log(plan_dir, f"Commit unit {selected_unit.number} needs_work before commit: {reason}")
        state.refresh_dashboard(source_repo)
        return {
            "unit": unit,
            "prompt_path": prompt_path,
            "action": "needs_work",
            "reason": reason,
            "commit": "",
            "changed_paths": changed,
            "out_of_scope_paths": outside_scope,
            "auto_resolved_dirty": auto_resolved_dirty,
            "repair_attempts": used_repair_attempts,
            "repair_reason": reason,
            "review_gate": review_gate_payload(review_result.review),
        }
    return prepare_and_finish_isolated_release(
        plan_dir=plan_dir,
        queue_data=queue_data,
        unit=unit,
        prompt_path=prompt_path,
        attempt_ledger=attempt_ledger,
        review_path=review_path,
        commit_number=selected_unit.number,
        changed=changed,
        commit_enabled=commit,
        commit_title=review_result.review.title,
        review_status=review_result.review.status,
        review_gate=review_gate_payload(review_result.review),
        review_summary=review_result.review.summary,
        used_repair_attempts=used_repair_attempts,
        last_repair_reason=last_repair_reason,
        auto_resolved_dirty=auto_resolved_dirty,
    )


def prepare_and_finish_isolated_release(
    *,
    plan_dir: Path,
    queue_data: dict,
    unit: dict,
    prompt_path: Path,
    attempt_ledger: AttemptLedger,
    review_path: Path,
    commit_number: int,
    changed: list[str],
    commit_enabled: bool,
    commit_title: str,
    review_status: str,
    review_gate: dict | None,
    review_summary: str,
    used_repair_attempts: int,
    last_repair_reason: str,
    auto_resolved_dirty: list[str],
) -> dict:
    repo = plans.execution_context_for_plan(plan_dir, queue_data).execution_repo
    snapshot = attempt_ledger.load()
    expected_parent = head_sha(repo)
    release_kind = "committed" if commit_enabled and changed else (
        "skipped" if not changed else "uncommitted"
    )
    message = commit_message(unit, commit_title)
    candidate_digest = worktree_patch_digest(repo, expected_parent, changed) if changed else ""
    final_revision = snapshot.revision + 2
    verification_digest = hashlib.sha256(
        json.dumps(
            {
                "attempt_ledger_revision": final_revision,
                "review_status": review_status,
                "review_gate": review_gate,
                "verification": [str(item) for item in unit.get("verification", [])],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    prepared = attempt_ledger.compare_and_set(
        snapshot.revision,
        {
            **snapshot.record,
            "release_state": "committing",
            "release_kind": release_kind,
            "release_action": "done" if release_kind == "uncommitted" else release_kind,
            "expected_parent": expected_parent,
            "changed_paths": list(changed),
            "changed_paths_sha256": _path_list_digest(changed),
            "changed_path_count": len(changed),
            "candidate_diff_sha256": candidate_digest,
            "message_sha256": _text_digest(message),
            "release_message": message,
            "release_out_of_scope_diff_sha256": out_of_scope_diff_digest(
                repo, [str(path) for path in unit.get("allowed_paths", [])]
            ),
            "verification_evidence_sha256": verification_digest,
            "release_review_gate": review_gate,
            "release_review_summary": review_summary,
            "release_repair_attempts": used_repair_attempts,
            "release_repair_reason": last_repair_reason,
        },
    )
    attempt_ledger.append_event(
        {
            "unit_id": unit["id"],
            "phase": "release",
            "event": "isolated_release_prepared",
            "ledger_revision": prepared.revision,
            "expected_head": expected_parent,
            "candidate_diff_sha256": candidate_digest,
            "changed_paths_sha256": _path_list_digest(changed),
            "changed_path_count": len(changed),
            "evidence_sha256": verification_digest,
            "message_sha256": _text_digest(message),
        }
    )
    return _finish_isolated_release(
        plan_dir=plan_dir,
        queue_data=queue_data,
        unit=unit,
        prompt_path=prompt_path,
        attempt_ledger=attempt_ledger,
        snapshot=prepared,
        review_path=review_path,
        commit_number=commit_number,
        auto_resolved_dirty=auto_resolved_dirty,
    )


def recover_isolated_release(
    *,
    plan_dir: Path,
    queue_data: dict,
    unit: dict,
    prompt_path: Path,
    commit_number: int,
) -> dict | None:
    ledger_path = _isolated_release_ledger_path(plan_dir, unit)
    if ledger_path is None:
        return None
    ledger = AttemptLedger(ledger_path)
    snapshot = ledger.load()
    record = snapshot.record
    if record.get("status") != "finalized":
        return None
    if not record.get("release_state") and record.get("release_commit"):
        snapshot = _upgrade_legacy_isolated_release(
            plan_dir=plan_dir,
            queue_data=queue_data,
            unit=unit,
            ledger=ledger,
            snapshot=snapshot,
        )
        record = snapshot.record
    if record.get("release_state") not in {"committing", "completed"}:
        return None
    attempt = int(unit.get("repair_attempts") or record.get("release_repair_attempts") or 0)
    review_path = plan_dir / "attempts" / unit["id"] / f"attempt-{attempt}-review.json"
    return _finish_isolated_release(
        plan_dir=plan_dir,
        queue_data=queue_data,
        unit=unit,
        prompt_path=prompt_path,
        attempt_ledger=ledger,
        snapshot=snapshot,
        review_path=review_path,
        commit_number=commit_number,
        auto_resolved_dirty=[],
    )


def _finish_isolated_release(
    *,
    plan_dir: Path,
    queue_data: dict,
    unit: dict,
    prompt_path: Path,
    attempt_ledger: AttemptLedger,
    snapshot,
    review_path: Path,
    commit_number: int,
    auto_resolved_dirty: list[str],
) -> dict:
    repo = plans.execution_context_for_plan(plan_dir, queue_data).execution_repo
    record = snapshot.record
    if record.get("release_state") == "committing":
        expected_parent = str(record.get("expected_parent") or "")
        changed = [str(path) for path in record.get("changed_paths", [])]
        observed_head = head_sha(repo)
        release_kind = str(record.get("release_kind") or "committed")
        if release_kind == "committed" and observed_head == expected_parent:
            if out_of_scope_diff_digest(
                repo, [str(path) for path in record.get("allowed_paths", [])]
            ) != record.get("release_out_of_scope_diff_sha256"):
                raise RuntimeError("isolated release out-of-scope baseline changed")
            if worktree_patch_digest(repo, expected_parent, changed) != record.get(
                "candidate_diff_sha256"
            ):
                raise RuntimeError("isolated release candidate changed before commit")
            commit_paths(repo, changed, str(record.get("release_message") or ""))
            observed_head = head_sha(repo)
        mismatch = _isolated_release_mismatch(repo, observed_head, record)
        if mismatch:
            raise RuntimeError(f"isolated release commit state is ambiguous: {mismatch}")
        try:
            snapshot = attempt_ledger.compare_and_set(
                snapshot.revision,
                {
                    **record,
                    "release_state": "completed",
                    "release_commit": observed_head if release_kind != "uncommitted" else "",
                    "release_observed_head": observed_head,
                    "release_tree": commit_tree(repo, observed_head),
                },
            )
        except LedgerConflict as exc:
            raise RuntimeError(str(exc)) from exc
        record = snapshot.record
    else:
        mismatch = _isolated_release_mismatch(repo, head_sha(repo), record)
        if mismatch:
            raise RuntimeError(f"completed isolated release proof is stale: {mismatch}")

    changed = [str(path) for path in record.get("changed_paths", [])]
    action = str(record.get("release_action") or record.get("release_kind") or "committed")
    commit_hash = str(record.get("release_commit") or "")
    unit.update(
        {
            "status": "done",
            "updated_at": state.timestamp(),
            "changed_paths": changed,
            "commit": commit_hash,
            "attempt_ledger_revision": snapshot.revision,
            "verification_evidence_sha256": str(
                record.get("verification_evidence_sha256") or ""
            ),
            "repair_attempts": int(record.get("release_repair_attempts") or 0),
        }
    )
    if record.get("release_review_gate"):
        unit["review_gate"] = record["release_review_gate"]
    if record.get("release_repair_reason"):
        unit["last_repair_reason"] = record["release_repair_reason"]
    ensure_attempt_view_revision(
        snapshot.revision,
        queue_unit=unit,
        review_path=review_path,
        handoff_path=attempt_ledger.path.parent / "handoff.json",
    )
    plans.save_queue(plan_dir, queue_data)
    completion = f"Completed commit unit {commit_number}."
    log_content = read_optional(plan_dir / "log.md")
    if completion not in log_content:
        details = [f"{action} {unit['id']}"]
        if changed:
            details.append(f"changed: {', '.join(changed)}")
        if commit_hash:
            details.append(f"commit: {commit_hash}")
        if record.get("release_review_summary"):
            details.append(f"summary: {record['release_review_summary']}")
        details.append(f"ledger_revision={snapshot.revision}")
        append_log(plan_dir, " | ".join(details))
        append_log(plan_dir, completion)
    attempt_ledger.append_event(
        {
            "unit_id": unit["id"],
            "phase": "recovery",
            "event": "isolated_release_reconciled",
            "ledger_revision": snapshot.revision,
            "expected_head": record.get("expected_parent"),
            "observed_head": head_sha(repo),
            "commit_sha": commit_hash,
            "commit_tree": record.get("release_tree"),
            "reason": str(record.get("release_state") or "legacy"),
        }
    )
    plan_readiness.sync_queue_cache_from_plan(plan_dir / "plan.md")
    state.refresh_dashboard(plans.execution_context_for_plan(plan_dir, queue_data).source_repo)
    return {
        "unit": unit,
        "prompt_path": prompt_path,
        "action": action,
        "commit": commit_hash,
        "changed_paths": changed,
        "auto_resolved_dirty": auto_resolved_dirty,
        "repair_attempts": int(record.get("release_repair_attempts") or 0),
        "repair_reason": str(record.get("release_repair_reason") or ""),
        "review_gate": record.get("release_review_gate"),
        "reconciled_release": True,
    }


def _isolated_release_mismatch(repo: Path, observed_head: str, record: dict) -> str:
    release_kind = str(record.get("release_kind") or "committed")
    expected_parent = str(record.get("expected_parent") or "")
    changed = tuple(str(path) for path in record.get("changed_paths", []))
    if release_kind == "uncommitted":
        if observed_head != expected_parent:
            return "uncommitted_head"
        if worktree_patch_digest(repo, expected_parent, list(changed)) != record.get(
            "candidate_diff_sha256"
        ):
            return "uncommitted_candidate"
        return ""
    if release_kind == "skipped":
        if observed_head != expected_parent or changed:
            return "skipped_state"
        return ""
    if commit_parent(repo, observed_head) != expected_parent:
        return "parent"
    observed_paths = changed_paths_between(repo, expected_parent, observed_head)
    if observed_paths != changed or _path_list_digest(observed_paths) != record.get(
        "changed_paths_sha256"
    ):
        return "paths"
    if binary_patch_digest(repo, expected_parent, list(changed), head=observed_head) != record.get(
        "candidate_diff_sha256"
    ):
        return "content"
    if _text_digest(git_commit_message(repo, observed_head)) != record.get("message_sha256"):
        return "message"
    if record.get("release_state") == "completed" and commit_tree(repo, observed_head) != record.get(
        "release_tree"
    ):
        return "tree"
    return ""


def _upgrade_legacy_isolated_release(
    *, plan_dir: Path, queue_data: dict, unit: dict, ledger: AttemptLedger, snapshot
):
    repo = plans.execution_context_for_plan(plan_dir, queue_data).execution_repo
    record = snapshot.record
    release_commit = str(record.get("release_commit") or "")
    if not release_commit or head_sha(repo) != release_commit:
        raise RuntimeError("legacy isolated release commit is not the exact current HEAD")
    expected_parent = str(record.get("expected_head") or "")
    if commit_parent(repo, release_commit) != expected_parent:
        raise RuntimeError("legacy isolated release parent is ambiguous")
    changed = list(changed_paths_between(repo, expected_parent, release_commit))
    outside = out_of_scope_paths(changed, [str(path) for path in record.get("allowed_paths", [])])
    if outside:
        raise RuntimeError("legacy isolated release contains out-of-scope paths")
    message = git_commit_message(repo, release_commit)
    return ledger.compare_and_set(
        snapshot.revision,
        {
            **record,
            "release_state": "completed",
            "release_kind": "committed",
            "release_action": "committed",
            "expected_parent": expected_parent,
            "changed_paths": changed,
            "changed_paths_sha256": _path_list_digest(changed),
            "changed_path_count": len(changed),
            "candidate_diff_sha256": binary_patch_digest(
                repo, expected_parent, changed, head=release_commit
            ),
            "message_sha256": _text_digest(message),
            "release_message": message,
            "release_observed_head": release_commit,
            "release_tree": commit_tree(repo, release_commit),
        },
    )


def _isolated_release_ledger_path(plan_dir: Path, unit: dict) -> Path | None:
    unit_id = str(unit.get("id") or "")
    if not unit_id:
        return None
    attempt = int(unit.get("repair_attempts") or 0)
    candidate = execution_attempt_dir(plan_dir, unit_id, attempt) / "attempt-ledger.json"
    if candidate.exists():
        return candidate
    attempts = sorted((plan_dir / "attempts" / unit_id).glob("attempt-*/attempt-ledger.json"))
    return attempts[-1] if attempts else None


def _path_list_digest(paths) -> str:
    return hashlib.sha256("\0".join(str(path) for path in paths).encode("utf-8")).hexdigest()


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def commit_message(unit: dict, title: str) -> str:
    base = title or unit.get("title") or unit.get("id") or "Codex Flow unit"
    return f"codex-flow: {unit.get('id', 'unit')} {base}"


def review_gate_payload(review: CommitUnitReview) -> dict | None:
    return review.gate.to_dict() if review.gate else None


def deterministic_unit_gate_result(
    implementation_session_id: str,
    unit: dict,
    expected_head: str,
    expected_digest: str,
) -> ReviewerAgentResult:
    review = CommitUnitReview(
        status="pass",
        title=str(unit.get("title") or unit.get("id") or "Codex Flow unit"),
        summary="deterministic scope and HEAD gate passed",
        gate=ReviewGate(status="pass", reason="deterministic_unit_gate"),
        retryable=False,
        failure_kind="deterministic_gate",
    )
    return ReviewerAgentResult(
        reviewer_session_id="",
        implementation_session_id=implementation_session_id,
        review_message="deterministic_unit_gate",
        review=review,
        expected_head=expected_head,
        observed_head=expected_head,
        expected_full_diff_digest=expected_digest,
        observed_full_diff_digest=expected_digest,
        review_mode="deterministic_unit_gate",
    )


def review_gate_summary(review: CommitUnitReview) -> str:
    if not review.gate:
        return "review_gate=legacy"
    gate = review.gate
    return f"review_gate={gate.status} score={gate.score} blockers={gate.blockers} important={gate.important} minor={gate.minor}"


def write_review_attempt(
    plan_dir: Path,
    unit_id: str,
    attempt: int,
    result: ReviewerAgentResult,
    *,
    child_attestation: str,
    ledger_revision: int,
) -> Path:
    review = result.review
    attempts_dir = plan_dir / "attempts" / unit_id
    attempts_dir.mkdir(parents=True, exist_ok=True)
    path = attempts_dir / f"attempt-{attempt}-review.json"
    payload = {
        "unit": unit_id,
        "attempt": attempt,
        "status": review.status,
        "title": review.title,
        "summary": review.summary,
        "reason": review.reason,
        "failure_kind": review.failure_kind,
        "review_gate": review_gate_payload(review),
        "review_mode": result.review_mode,
        "sessions_distinct": bool(result.reviewer_session_id)
        and result.reviewer_session_id != result.implementation_session_id,
        "implementation_session_sha256": session_digest(result.implementation_session_id),
        "reviewer_session_sha256": session_digest(result.reviewer_session_id),
        "expected_head": result.expected_head,
        "observed_head": result.observed_head,
        "head_unchanged": result.head_unchanged,
        "expected_full_diff_digest": result.expected_full_diff_digest,
        "observed_full_diff_digest": result.observed_full_diff_digest,
        "full_diff_digest_unchanged": result.full_diff_digest_unchanged,
        "child_attestation": child_attestation,
        "ledger_revision": ledger_revision,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_review_failure_attempt(
    plan_dir: Path,
    unit_id: str,
    attempt: int,
    error: ReviewerProcessFailure,
    *,
    implementation_session_id: str,
    child_attestation: str,
    ledger_revision: int,
) -> Path:
    attempts_dir = plan_dir / "attempts" / unit_id
    attempts_dir.mkdir(parents=True, exist_ok=True)
    path = attempts_dir / f"attempt-{attempt}-review.json"
    payload = {
        "unit": unit_id,
        "attempt": attempt,
        "status": "needs_work",
        "title": "",
        "summary": "",
        "reason": str(error),
        "failure_kind": "protocol_failure" if not error.head_unchanged or not error.full_diff_digest_unchanged else "process_failure",
        "review_gate": None,
        "review_mode": "fresh_read_only",
        "sessions_distinct": None,
        "implementation_session_sha256": session_digest(implementation_session_id),
        "reviewer_session_sha256": "",
        "expected_head": error.expected_head,
        "observed_head": error.observed_head,
        "head_unchanged": error.head_unchanged,
        "expected_full_diff_digest": error.expected_full_diff_digest,
        "observed_full_diff_digest": error.observed_full_diff_digest,
        "full_diff_digest_unchanged": error.full_diff_digest_unchanged,
        "process_error": type(error.original).__name__,
        "child_attestation": child_attestation,
        "ledger_revision": ledger_revision,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def session_digest(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest() if session_id else ""


def execution_attempt_dir(plan_dir: Path, unit_id: str, attempt: int) -> Path:
    return plan_dir / "attempts" / unit_id / f"attempt-{attempt}"


def write_attempt_handoff(
    attempt_dir: Path,
    *,
    unit_id: str,
    ledger_revision: int,
    status: str,
    next_action: str,
) -> Path:
    path = attempt_dir / "handoff.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "unit": unit_id,
        "ledger_revision": ledger_revision,
        "status": status,
        "next_action": next_action,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def ensure_attempt_view_revision(
    expected_revision: int,
    *,
    queue_unit: dict,
    review_path: Path,
    handoff_path: Path,
) -> None:
    observed = {
        "queue": int(queue_unit.get("attempt_ledger_revision") or -1),
        "review": int(json.loads(review_path.read_text(encoding="utf-8")).get("ledger_revision", -1)),
        "handoff": int(json.loads(handoff_path.read_text(encoding="utf-8")).get("ledger_revision", -1)),
    }
    mismatches = {name: revision for name, revision in observed.items() if revision != expected_revision}
    if not mismatches:
        return
    queue_unit["attempt_ledger_revision"] = expected_revision
    for path in (review_path, handoff_path):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["ledger_revision"] = expected_revision
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_all(
    plan_path: str | Path,
    max_units: int | None = None,
    dry_run: bool = False,
    execute: bool = False,
    commit: bool = False,
    codex_command: str = "codex",
    codex_args: list[str] | None = None,
    allow_dirty: bool = False,
    no_branch: bool = False,
    auto_resolve: bool = False,
    repair_attempts: int = 0,
    accept_source_drift: bool = False,
    codex_timeout_seconds: int | None = None,
) -> list[dict]:
    results: list[dict] = []
    limit = max_units
    if limit is None and not execute:
        limit = 4
    while limit is None or len(results) < limit:
        result = run_next(
            plan_path,
            dry_run=dry_run,
            execute=execute,
            commit=commit,
            codex_command=codex_command,
            codex_args=codex_args or [],
            allow_dirty=allow_dirty,
            no_branch=no_branch,
            auto_resolve=auto_resolve,
            repair_attempts=repair_attempts,
            accept_source_drift=accept_source_drift,
            codex_timeout_seconds=codex_timeout_seconds,
        )
        if result is None:
            break
        results.append(result)
        if dry_run or result.get("action") in {"human_gate", "main_handoff", "needs_work", "source_drift"}:
            break
    return results


def execution_attempts(
    repair_attempts: int,
    *,
    resume_needs_work: bool,
    previous_repair_attempts: int,
) -> list[int]:
    budget = max(0, repair_attempts)
    if resume_needs_work:
        return list(range(previous_repair_attempts + 1, previous_repair_attempts + max(1, budget) + 1))
    return list(range(budget + 1))


def merge_changed_paths(*path_groups: list[str]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for group in path_groups:
        for path in group:
            if path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def repair_changed_paths(preserved_paths: list[str], before, after) -> list[str]:
    return merge_changed_paths(active_snapshot_paths(preserved_paths, after), changed_paths_since(before, after))


def active_snapshot_paths(paths: list[str], snapshot) -> list[str]:
    active = {entry.path for entry in snapshot.entries}
    return [path for path in paths if path in active]


def append_log(plan_dir: Path, message: str) -> None:
    log_path = plan_dir / "log.md"
    current = log_path.read_text(encoding="utf-8") if log_path.exists() else "# Log\n"
    log_path.write_text(current.rstrip() + f"\n- {state.timestamp()} {message}\n", encoding="utf-8")
