from __future__ import annotations

from pathlib import Path

from . import plan_readiness, plans, state
from .git_ops import changed_paths_since, commit_paths, dirty_paths, head_summary, prepare_branch, stash_paths, status
from .implementer_agent import CodexImplementerAgent, ImplementerAgentInput


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
    allowed = "\n".join(f"- {item}" for item in unit.get("allowed_paths", [])) or "- Not specified"
    verification = "\n".join(f"- {item}" for item in unit.get("verification", [])) or "- Not specified"
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
            "## Hard Stop Gates",
            "",
            "- Do not create or merge a real remote PR.",
            "- Do not deploy.",
            "- Do not reset, checkout, or revert unrelated user changes.",
            "- If secrets, accounts, payments, or external posting are required, stop after preparing the draft.",
            "",
            "## Execution Contract",
            "",
            "Implement only this unit. Keep the diff narrow. Do not create a git commit; Codex Flow will commit after review.",
            "",
            "Return one final line in one of these forms:",
            f'COMMIT_UNIT_READY title="{unit["title"]}" summary="..."',
            'COMMIT_UNIT_NEEDS_WORK reason="..."',
            "",
            "## Context",
            "",
            f"- Branch: {queue_data.get('branch', '-')}",
            f"- Previous commit: {head_summary(plan_dir.parents[2]) if (plan_dir.parents[2] / '.git').exists() else 'None'}",
            "",
            selected,
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
    else:
        unit = next_ready_unit(queue_data)
        if unit is None:
            return None
        commit_unit = commit_units.get(unit["id"])

    prompt_path = plan_dir / "prompts" / f"{unit['id']}.md"
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
        )

    unit["status"] = "prompted"
    unit["prompt_path"] = str(prompt_path.relative_to(plan_dir))
    unit["updated_at"] = state.timestamp()
    plans.save_queue(plan_dir, queue_data)
    append_log(plan_dir, f"Prompted commit unit {unit.get('number') or unit['id']}: {unit['title']} -> {unit['prompt_path']}")
    state.refresh_dashboard(plan_dir.parents[2])
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
) -> dict:
    repo = plan_dir.parents[2]
    branch = queue_data.get("branch") or f"codex/{queue_data.get('plan_slug', 'plan')}"
    initial_status = status(repo)
    dirty = dirty_paths(initial_status)
    auto_resolved_dirty: list[str] = []
    if dirty and not allow_dirty:
        if not auto_resolve:
            raise SystemExit(f"Working tree must be clean before execute, excluding .codex-flow: {', '.join(dirty)}")
        message = f"codex-flow auto-shelve before {unit['id']} {state.timestamp()}"
        stash_paths(repo, dirty, message)
        auto_resolved_dirty = dirty
        append_log(plan_dir, f"auto_resolved dirty_worktree for {unit['id']} by stash: {', '.join(dirty)}")
    if not no_branch:
        prepare_branch(repo, branch)
    before = status(repo)
    unit["status"] = "in_progress"
    unit["prompt_path"] = str(prompt_path.relative_to(plan_dir))
    unit["updated_at"] = state.timestamp()
    plans.save_queue(plan_dir, queue_data)
    append_log(plan_dir, f"Started commit unit {unit.get('number') or unit['id']}: {unit['title']} on {branch}.")

    selected_unit = commit_unit or plan_readiness.CommitUnit(number=unit.get("number") or int(str(unit["id"]).split("-")[-1]), title=unit["title"], content="")
    agent_result = CodexImplementerAgent(command=codex_command, extra_args=codex_args).implement(
        ImplementerAgentInput(
            repo=repo,
            plan_path=plan_dir / "plan.md",
            plan_content=(plan_dir / "plan.md").read_text(encoding="utf-8"),
            unit=selected_unit,
            previous_commit=head_summary(repo),
            git_status=before.raw,
        )
    )
    if agent_result.review.status == "needs_work":
        unit["status"] = "needs_work"
        unit["updated_at"] = state.timestamp()
        plans.save_queue(plan_dir, queue_data)
        append_log(plan_dir, f"Commit unit {selected_unit.number} needs_work: {agent_result.review.reason}")
        state.refresh_dashboard(repo)
        return {
            "unit": unit,
            "prompt_path": prompt_path,
            "action": "needs_work",
            "reason": agent_result.review.reason,
            "changed_paths": [],
            "auto_resolved_dirty": auto_resolved_dirty,
        }

    after = status(repo)
    changed = changed_paths_since(before, after)
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
    plans.save_queue(plan_dir, queue_data)
    log_parts = [f"{action} {unit['id']}"]
    if changed:
        log_parts.append(f"changed: {', '.join(changed)}")
    if commit_hash:
        log_parts.append(f"commit: {commit_hash}")
    if agent_result.review.summary:
        log_parts.append(f"summary: {agent_result.review.summary}")
    append_log(plan_dir, " | ".join(log_parts))
    append_log(plan_dir, f"Completed commit unit {selected_unit.number}.")
    queue_data = plan_readiness.sync_queue_cache_from_plan(plan_dir / "plan.md")
    state.refresh_dashboard(repo)
    return {
        "unit": unit,
        "prompt_path": prompt_path,
        "action": action,
        "commit": commit_hash,
        "changed_paths": changed,
        "auto_resolved_dirty": auto_resolved_dirty,
    }


def commit_message(unit: dict, title: str) -> str:
    base = title or unit.get("title") or unit.get("id") or "Codex Flow unit"
    return f"codex-flow: {unit.get('id', 'unit')} {base}"


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
        )
        if result is None:
            break
        results.append(result)
        if dry_run or result.get("action") == "needs_work":
            break
    return results


def append_log(plan_dir: Path, message: str) -> None:
    log_path = plan_dir / "log.md"
    current = log_path.read_text(encoding="utf-8") if log_path.exists() else "# Log\n"
    log_path.write_text(current.rstrip() + f"\n- {state.timestamp()} {message}\n", encoding="utf-8")
