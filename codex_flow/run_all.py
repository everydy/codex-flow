from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import plan_readiness, plans, pr, runner
from .merge import MergeRunner


@dataclass(frozen=True)
class RunAllResult:
    action: str
    steps: list[dict]
    message: str


class RunAllRunner:
    def run_all(
        self,
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
        open_pr: bool = False,
        merge: bool = False,
        remote: bool = False,
        target: str = "main",
        no_merge: bool = False,
    ) -> RunAllResult:
        steps = runner.run_all(
            plan_path,
            max_units=max_units,
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
        if any(step.get("action") == "source_drift" for step in steps):
            return RunAllResult("source_drift", steps, "run_all: source_drift")
        if any(step.get("action") == "human_gate" for step in steps):
            return RunAllResult("human_gate", steps, "run_all: human_gate")
        if any(step.get("action") == "needs_work" for step in steps):
            return RunAllResult("needs_work", steps, "run_all: needs_work")
        if any(step.get("action") == "main_handoff" for step in steps):
            return RunAllResult("main_handoff", steps, "run_all: main agent transaction opened")
        plan_dir, plan_content, log_content = plan_readiness.read_plan_file(plan_path)
        readiness = plan_readiness.check_plan_ready(plan_content, log_content)
        if not readiness.ready:
            if max_units is not None and len(steps) >= max_units:
                return RunAllResult("max_units_reached", steps, f"run_all: max_units_reached remaining={readiness.next_unit.number if readiness.next_unit else 'unknown'}")
            if not execute and not open_pr and not merge and not remote:
                next_unit = readiness.next_unit.number if readiness.next_unit else "unknown"
                return RunAllResult("prompts_generated", steps, f"run_all: prompts_generated remaining={next_unit}")
            return RunAllResult("not_ready", steps, readiness.reason)
        _, queue = plans.load_queue(plan_path)
        if remote:
            lock_path = pr.read_pr_lock(plans.execution_context_for_plan(plan_dir, queue).source_repo)
            if lock_path:
                return RunAllResult("pr_locked", steps, pr.format_active_pr_lock(lock_path))
        if merge or remote or (execute and not no_merge and not open_pr):
            try:
                pr.require_adaptive_final_gate(plan_dir, queue)
            except Exception as exc:
                return RunAllResult("needs_work", steps, f"final_gate: {exc}")
        if merge or (execute and not no_merge and not open_pr and not remote):
            merge_result = MergeRunner().merge_remote(plan_path, target=target, execute=True) if remote else MergeRunner().merge_local(plan_path, target=target, execute=True)
            return RunAllResult(merge_result.action, steps, merge_result.message)
        if remote:
            try:
                url, lock_path = pr.create_remote_pr(plan_path, draft=True)
            except pr.ActivePrLockError as exc:
                return RunAllResult("pr_locked", steps, str(exc))
            except SystemExit as exc:
                return RunAllResult("needs_work", steps, str(exc))
            return RunAllResult("opened", steps, f"opened_pr: {url}\npr_lock: {lock_path}")
        if open_pr:
            pr_path = pr.write_pr_dry_run(plan_path)
            return RunAllResult("pr_dry_run", steps, f"pr_dry_run: {pr_path}")
        branch = plan_readiness.branch_name_from_plan(plan_content, f"codex/{plan_dir.name}")
        return RunAllResult("local_branch", steps, f"local_branch: {branch}; merge was not requested")
