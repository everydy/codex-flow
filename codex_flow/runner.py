from __future__ import annotations

import json
import os
from pathlib import Path

from . import plan_readiness, plans, source_plan, state
from .codex_cli import (
    ChildAttestationError,
    ChildRuntimeConfigError,
    CodexExecFailure,
    CodexExecTimeout,
    generate_child_attestation,
    verify_child_attestation,
)
from .child_runtime import ensure_prepared_child_runtime
from .git_ops import changed_paths_since, commit_paths, dirty_paths, head_summary, prepare_branch, scoped_status_summary, stash_paths, status
from .implementer_agent import CommitUnitReview, CodexImplementerAgent, ImplementerAgentInput, POST_UNIT_REVIEW_SKILL
from .reviewer_agent import out_of_scope_paths, require_post_unit_review, required_review_skills


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
            "",
            "## Execution Contract",
            "",
            "Implement only this unit. Keep the diff narrow. Do not create a git commit; Codex Flow will commit after review.",
            f"Before a commit can be created, the post-unit review must load and apply `{POST_UNIT_REVIEW_SKILL}`.",
            "Blocker or important findings from that review must be fixed in the review pass or returned as `COMMIT_UNIT_NEEDS_WORK`.",
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
    plan_dir, queue_data = plans.load_queue(plan_path)
    queue_data = plan_readiness.sync_queue_cache_from_plan(plan_dir / "plan.md")
    plan_content = (plan_dir / "plan.md").read_text(encoding="utf-8")
    commit_units = {item.unit_id: item for item in plan_readiness.parse_commit_units(plan_content)}
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
    prompt_text = render_prompt(queue_data, unit, plan_dir, commit_unit)
    if dry_run:
        return {"unit": unit, "prompt_path": prompt_path, "prompt": prompt_text, "changed": False}

    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt_text, encoding="utf-8")
    if execute:
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
) -> dict:
    execution_context = plans.execution_context_for_plan(plan_dir, queue_data)
    repo = execution_context.execution_repo
    source_repo = execution_context.source_repo
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
        runtime = ensure_prepared_child_runtime(
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
    if not no_branch:
        prepare_branch(repo, branch)
    before_head = head_summary(repo)
    before = status(repo)
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
    agent_result = None
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
        try:
            agent_result = agent.implement(
                ImplementerAgentInput(
                    repo=repo,
                    plan_path=plan_dir / "plan.md",
                    plan_content=(plan_dir / "plan.md").read_text(encoding="utf-8"),
                    unit=selected_unit,
                    previous_commit=head_summary(repo),
                    git_status=scoped_status_summary(status(repo), [str(path) for path in unit.get("allowed_paths", [])]),
                    repair_attempt=attempt,
                    repair_reason=last_repair_reason,
                ),
                diagnostic_dir=attempt_dir,
                timeout_seconds=codex_timeout_seconds,
            )
        except (CodexExecTimeout, CodexExecFailure) as exc:
            partial_changed = repair_changed_paths(preserved_repair_dirty, before, status(repo))
            unit["status"] = "needs_work"
            unit["updated_at"] = state.timestamp()
            unit["repair_attempts"] = used_repair_attempts
            unit["last_needs_work_reason"] = str(exc)
            unit["diagnostic_path"] = str(exc.diagnostic_dir.relative_to(plan_dir))
            unit["changed_paths"] = partial_changed
            plans.save_queue(plan_dir, queue_data)
            append_log(plan_dir, f"Commit unit {selected_unit.number} needs_work: {exc}")
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
        agent_result = replace_agent_review(
            agent_result,
            require_post_unit_review(agent_result.review, agent_result.review_message),
        )
        write_review_attempt(
            plan_dir,
            unit["id"],
            attempt,
            agent_result.review,
            child_attestation=str(attestation_path.relative_to(plan_dir)),
        )
        if agent_result.review.gate:
            unit["review_gate"] = agent_result.review.gate.to_dict()
            append_log(plan_dir, f"Review gate for commit unit {selected_unit.number} attempt {attempt}: {review_gate_summary(agent_result.review)}")
        if agent_result.review.status != "needs_work":
            break
        last_repair_reason = agent_result.review.reason
        if not agent_result.review.retryable:
            unit["status"] = "needs_work"
            unit["updated_at"] = state.timestamp()
            unit["repair_attempts"] = used_repair_attempts
            unit["last_needs_work_reason"] = last_repair_reason
            if agent_result.review.gate:
                unit["review_gate"] = agent_result.review.gate.to_dict()
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
                "review_gate": review_gate_payload(agent_result.review),
            }
        if index < len(attempts) - 1:
            append_log(plan_dir, f"Commit unit {selected_unit.number} requested repair: {last_repair_reason}")
            continue
        unit["status"] = "needs_work"
        unit["updated_at"] = state.timestamp()
        unit["repair_attempts"] = used_repair_attempts
        unit["last_needs_work_reason"] = last_repair_reason
        if agent_result.review.gate:
            unit["review_gate"] = agent_result.review.gate.to_dict()
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
            "review_gate": review_gate_payload(agent_result.review),
        }
    if agent_result is None:
        raise SystemExit("Codex implementer did not return a result")

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
        if agent_result.review.gate:
            unit["review_gate"] = agent_result.review.gate.to_dict()
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
            "review_gate": review_gate_payload(agent_result.review),
        }
    outside_scope = out_of_scope_paths(changed, [str(path) for path in unit.get("allowed_paths", [])])
    if outside_scope:
        reason = f"unit changed paths outside allowed scope: {', '.join(outside_scope)}"
        unit["status"] = "needs_work"
        unit["updated_at"] = state.timestamp()
        unit["repair_attempts"] = used_repair_attempts
        unit["last_needs_work_reason"] = reason
        unit["changed_paths"] = changed
        if agent_result.review.gate:
            unit["review_gate"] = agent_result.review.gate.to_dict()
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
            "review_gate": review_gate_payload(agent_result.review),
        }
    commit_hash = ""
    action = "done"
    if commit and changed:
        commit_hash = commit_paths(repo, changed, commit_message(unit, agent_result.review.title))
        action = "committed"
    elif not changed:
        action = "skipped"

    unit["status"] = "done"
    unit["updated_at"] = state.timestamp()
    unit["changed_paths"] = changed
    unit["commit"] = commit_hash
    unit["repair_attempts"] = used_repair_attempts
    if agent_result.review.gate:
        unit["review_gate"] = agent_result.review.gate.to_dict()
    if last_repair_reason:
        unit["last_repair_reason"] = last_repair_reason
    plans.save_queue(plan_dir, queue_data)
    log_parts = [f"{action} {unit['id']}"]
    if changed:
        log_parts.append(f"changed: {', '.join(changed)}")
    if commit_hash:
        log_parts.append(f"commit: {commit_hash}")
    if agent_result.review.summary:
        log_parts.append(f"summary: {agent_result.review.summary}")
    if agent_result.review.gate:
        log_parts.append(review_gate_summary(agent_result.review))
    if used_repair_attempts:
        log_parts.append(f"repair_attempts: {used_repair_attempts}")
    append_log(plan_dir, " | ".join(log_parts))
    if used_repair_attempts:
        append_log(plan_dir, f"Repair succeeded for commit unit {selected_unit.number} after {used_repair_attempts} attempt(s).")
    append_log(plan_dir, f"Completed commit unit {selected_unit.number}.")
    queue_data = plan_readiness.sync_queue_cache_from_plan(plan_dir / "plan.md")
    state.refresh_dashboard(source_repo)
    return {
        "unit": unit,
        "prompt_path": prompt_path,
        "action": action,
        "commit": commit_hash,
        "changed_paths": changed,
        "auto_resolved_dirty": auto_resolved_dirty,
        "repair_attempts": used_repair_attempts,
        "repair_reason": last_repair_reason,
        "review_gate": review_gate_payload(agent_result.review),
    }


def commit_message(unit: dict, title: str) -> str:
    base = title or unit.get("title") or unit.get("id") or "Codex Flow unit"
    return f"codex-flow: {unit.get('id', 'unit')} {base}"


def review_gate_payload(review: CommitUnitReview) -> dict | None:
    return review.gate.to_dict() if review.gate else None


def review_gate_summary(review: CommitUnitReview) -> str:
    if not review.gate:
        return "review_gate=legacy"
    gate = review.gate
    return f"review_gate={gate.status} score={gate.score} blockers={gate.blockers} important={gate.important} minor={gate.minor}"


def replace_agent_review(agent_result, review: CommitUnitReview):
    return type(agent_result)(
        session_id=agent_result.session_id,
        implementation_message=agent_result.implementation_message,
        review_message=agent_result.review_message,
        review=review,
    )


def write_review_attempt(
    plan_dir: Path,
    unit_id: str,
    attempt: int,
    review: CommitUnitReview,
    *,
    child_attestation: str,
) -> Path:
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
        "review_gate": review_gate_payload(review),
        "review_skill": POST_UNIT_REVIEW_SKILL,
        "child_attestation": child_attestation,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def execution_attempt_dir(plan_dir: Path, unit_id: str, attempt: int) -> Path:
    return plan_dir / "attempts" / unit_id / f"attempt-{attempt}"


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
        if dry_run or result.get("action") in {"human_gate", "needs_work", "source_drift"}:
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
