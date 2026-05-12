from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import plan_readiness, pr, runner
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
        open_pr: bool = False,
        merge: bool = False,
        remote: bool = False,
        target: str = "main",
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
        )
        if any(step.get("action") == "needs_work" for step in steps):
            return RunAllResult("needs_work", steps, "run_all: needs_work")
        plan_dir, plan_content, log_content = plan_readiness.read_plan_file(plan_path)
        readiness = plan_readiness.check_plan_ready(plan_content, log_content)
        if not readiness.ready:
            if max_units is not None and len(steps) >= max_units:
                return RunAllResult("max_units_reached", steps, f"run_all: max_units_reached remaining={readiness.next_unit.number if readiness.next_unit else 'unknown'}")
            return RunAllResult("not_ready", steps, readiness.reason)
        if merge:
            merge_result = MergeRunner().merge_remote(plan_path, target=target, execute=True) if remote else MergeRunner().merge_local(plan_path, target=target, execute=True)
            return RunAllResult(merge_result.action, steps, merge_result.message)
        if remote:
            url, lock_path = pr.create_remote_pr(plan_path, draft=True)
            return RunAllResult("opened", steps, f"opened_pr: {url}\npr_lock: {lock_path}")
        if open_pr:
            pr_path = pr.write_pr_dry_run(plan_path)
            return RunAllResult("pr_dry_run", steps, f"pr_dry_run: {pr_path}")
        branch = plan_readiness.branch_name_from_plan(plan_content, f"codex/{plan_dir.name}")
        return RunAllResult("local_branch", steps, f"local_branch: {branch}; remote PR was not opened")
