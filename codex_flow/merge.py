from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re

from . import plan_readiness, state
from .git_ops import (
    command_failure,
    commit_merge,
    delete_local_branch,
    dirty_paths,
    fetch_branch,
    has_pending_merge_commit,
    merge_branch,
    merge_current_branch,
    push_branch,
    run_process,
    status,
    unmerged_paths,
)
from .merge_agent import CodexMergeAgent, MergeAgent, MergeAgentInput
from .final_gate import FinalizeGuard, FinalGateError


@dataclass(frozen=True)
class MergeResult:
    action: str
    message: str
    source_branch: str = ""
    target_branch: str = ""
    pr_url: str = ""


class MergeRunner:
    def __init__(self, agent: MergeAgent | None = None, gh_command: str = "gh") -> None:
        self.agent = agent or CodexMergeAgent()
        self.gh_command = gh_command

    def merge_local(self, plan_path: str | Path, target: str = "main", execute: bool = False) -> MergeResult:
        plan_dir, plan_content, log_content = plan_readiness.read_plan_file(plan_path)
        readiness = plan_readiness.check_plan_ready(plan_content, log_content)
        branch = plan_readiness.branch_name_from_plan(plan_content, f"codex/{plan_dir.name}")
        if not readiness.ready:
            return MergeResult("needs_work", "merge: needs_work plan is not complete", branch, target)
        if not execute:
            return MergeResult("hard_stop", "merge: hard-stop use --execute to run an actual merge", branch, target)
        try:
            FinalizeGuard.require(plan_dir / "plan.md")
        except FinalGateError as exc:
            return MergeResult("needs_work", f"final_gate: {exc}", branch, target)
        repo = plan_dir.parents[2]
        dirty = dirty_paths(status(repo))
        if dirty:
            return MergeResult("needs_work", f"merge: needs_work dirty worktree: {', '.join(dirty)}", branch, target)
        result = merge_branch(repo, branch, target)
        if result.status == 0:
            append_merge_log(plan_dir, f"Merged local branch `{branch}` into `{target}`.")
            return self.finish_local_merge(repo, plan_path, plan_dir, branch, target)
        resolved = self.resolve_conflict(repo, plan_dir / "plan.md", branch, target, "local", command_failure("git merge failed", result))
        if resolved.action == "merge_needs_work":
            append_merge_log(plan_dir, resolved.message)
            return resolved
        append_merge_log(plan_dir, resolved.message)
        return self.finish_local_merge(repo, plan_path, plan_dir, branch, target)

    def merge_remote(self, plan_path: str | Path, target: str = "main", execute: bool = False) -> MergeResult:
        plan_dir, plan_content, log_content = plan_readiness.read_plan_file(plan_path)
        readiness = plan_readiness.check_plan_ready(plan_content, log_content)
        branch = plan_readiness.branch_name_from_plan(plan_content, f"codex/{plan_dir.name}")
        if not readiness.ready:
            return MergeResult("needs_work", "merge: needs_work plan is not complete", branch, target)
        if not execute:
            return MergeResult("hard_stop", "merge: hard-stop use --execute to run an actual merge", branch, target)
        try:
            FinalizeGuard.require(plan_dir / "plan.md")
        except FinalGateError as exc:
            return MergeResult("needs_work", f"final_gate: {exc}", branch, target)
        repo = plan_dir.parents[2]
        active_lock = read_active_pr_lock(repo)
        if active_lock and active_lock[1] != branch:
            return MergeResult("needs_work", f"merge: needs_work pr_locked active branch={active_lock[1]} lock={active_lock[0]}", branch, target)
        push_branch(repo, branch)
        pr_url = self.ensure_remote_pr(repo, branch, target, plan_content)
        merge_result = run_process([self.gh_command, "pr", "merge", pr_url, "--merge"], cwd=repo)
        if merge_result.status != 0 and is_retryable_remote_merge_failure(merge_result.stderr + merge_result.stdout):
            retry_result = self.rebase_source_on_target(repo, plan_dir / "plan.md", branch, target, command_failure("gh pr merge failed", merge_result))
            if retry_result.action == "merge_needs_work":
                append_merge_log(plan_dir, retry_result.message)
                return retry_result
            push_branch(repo, branch)
            merge_result = run_process([self.gh_command, "pr", "merge", pr_url, "--merge"], cwd=repo)
        if merge_result.status != 0:
            return MergeResult("needs_work", command_failure("gh pr merge failed", merge_result), branch, target, pr_url)
        lock_cleared = clear_matching_pr_lock(repo, branch)
        append_merge_log(plan_dir, f"Merged remote PR `{pr_url}`.")
        if lock_cleared:
            append_merge_log(plan_dir, f"Cleared PR lock for `{branch}`.")
        fetch = fetch_branch(repo, target)
        if fetch.status != 0:
            message = command_failure("git fetch failed after remote merge", fetch)
            append_merge_log(plan_dir, f"Held local branch `{branch}` after remote merge: {message}.")
            return MergeResult("branch_finalization_blocked", message, branch, target, pr_url)
        convergence = merge_branch(repo, f"origin/{target}", target)
        if convergence.status != 0:
            message = command_failure("local target convergence failed after remote merge", convergence)
            append_merge_log(plan_dir, f"Held local branch `{branch}` after remote merge: {message}.")
            return MergeResult("branch_finalization_blocked", message, branch, target, pr_url)
        finalized, close_message = self.finalize_source_branch(repo, plan_path, plan_dir, branch, target)
        suffixes = []
        if lock_cleared:
            suffixes.append("pr_lock_cleared=true")
        if close_message:
            suffixes.append(close_message)
        suffix = f"; {'; '.join(suffixes)}" if suffixes else ""
        action = "merged_remote" if finalized else "branch_finalization_blocked"
        return MergeResult(action, f"merge: remote merged {pr_url}{suffix}", branch, target, pr_url)

    def ensure_remote_pr(self, repo: Path, branch: str, target: str, plan_content: str) -> str:
        existing = run_process(
            [
                self.gh_command,
                "pr",
                "list",
                "--head",
                branch,
                "--base",
                target,
                "--state",
                "open",
                "--json",
                "url,title,isDraft",
                "--limit",
                "1",
            ],
            cwd=repo,
        )
        if existing.status == 0:
            pr = parse_existing_pr(existing.stdout)
            if pr:
                if pr.get("isDraft"):
                    ready = run_process([self.gh_command, "pr", "ready", pr["url"]], cwd=repo)
                    if ready.status != 0:
                        raise SystemExit(command_failure("gh pr ready failed", ready))
                return pr["url"]
        title = plan_readiness.title_from_plan(plan_content, branch)
        created = run_process(
            [self.gh_command, "pr", "create", "--head", branch, "--base", target, "--title", title, "--body", build_remote_pr_body(plan_content, branch)],
            cwd=repo,
        )
        if created.status != 0:
            raise SystemExit(command_failure("gh pr create failed", created))
        url = parse_pr_url(created.stdout + "\n" + created.stderr)
        if not url:
            raise SystemExit("Could not parse PR URL from gh output")
        return url

    def rebase_source_on_target(self, repo: Path, plan_path: Path, branch: str, target: str, failure: str) -> MergeResult:
        fetch = fetch_branch(repo, target)
        if fetch.status != 0:
            return MergeResult("merge_needs_work", command_failure("git fetch failed", fetch), branch, target)
        switch = run_process(["git", "switch", branch], cwd=repo)
        if switch.status != 0:
            return MergeResult("merge_needs_work", command_failure("git switch failed", switch), branch, target)
        merge = merge_current_branch(repo, f"origin/{target}")
        if merge.status == 0:
            return MergeResult("merged_local", f"merge: source branch updated with origin/{target}", branch, target)
        return self.resolve_conflict(repo, plan_path, branch, target, "remote", failure)

    def resolve_conflict(self, repo: Path, plan_path: Path, branch: str, target: str, mode: str, failure: str) -> MergeResult:
        agent_result = self.agent.resolve_conflicts(
            MergeAgentInput(
                repo=repo,
                plan_path=plan_path,
                source_branch=branch,
                target_branch=target,
                merge_mode=mode,
                git_status=status(repo).raw,
                failed_merge_command=failure,
            )
        )
        if agent_result.status != "ready":
            return MergeResult("merge_needs_work", f"merge_needs_work: {agent_result.reason}", branch, target)
        remaining = unmerged_paths(repo)
        if remaining:
            return MergeResult("merge_needs_work", f"merge_needs_work: unresolved paths: {', '.join(remaining)}", branch, target)
        if has_pending_merge_commit(repo):
            commit = commit_merge(repo)
            if commit.status != 0:
                return MergeResult("merge_needs_work", command_failure("git merge commit failed", commit), branch, target)
        return MergeResult("merge_ready", f"merge: conflicts resolved: {agent_result.summary}", branch, target)

    def finish_local_merge(
        self,
        repo: Path,
        plan_path: str | Path,
        plan_dir: Path,
        branch: str,
        target: str,
    ) -> MergeResult:
        finalized, close_message = self.finalize_source_branch(repo, plan_path, plan_dir, branch, target)
        suffix = f"; {close_message}" if close_message else ""
        action = "merged_local" if finalized else "branch_finalization_blocked"
        return MergeResult(action, f"merge: local merged {branch} into {target}{suffix}", branch, target)

    def finalize_source_branch(
        self,
        repo: Path,
        plan_path: str | Path,
        plan_dir: Path,
        branch: str,
        target: str,
    ) -> tuple[bool, str]:
        from . import plans

        try:
            _, queue = plans.load_queue(plan_path)
            context = plans.execution_context_for_plan(plan_dir, queue)
        except (FileNotFoundError, SystemExit, ValueError):
            return self.close_source_branch(repo, plan_dir, branch, target)

        if context.execution_repo == context.source_repo:
            return self.close_source_branch(repo, plan_dir, branch, target)

        try:
            cleaned = plans.cleanup_plan_worktree(plan_path, target_branch=target)
        except SystemExit as exc:
            message = f"branch_finalization_blocked: {exc}"
            append_merge_log(plan_dir, f"Held execution worktree and local branch `{branch}` after merge: {exc}.")
            return False, message

        message = f"worktree_cleaned: {cleaned.worktree_path}; branch_closed: {branch}"
        append_merge_log(plan_dir, f"Finalized execution worktree and local branch `{branch}` after merge.")
        return True, message

    def close_source_branch(self, repo: Path, plan_dir: Path, branch: str, target: str) -> tuple[bool, str]:
        if branch == target or branch in {"main", "master", "develop", "production", "release"}:
            return True, "branch_close: skipped protected_or_target"
        result = delete_local_branch(repo, branch)
        if result.status == 0:
            append_merge_log(plan_dir, f"Closed local branch `{branch}` with `git branch -d`.")
            return True, f"branch_closed: {branch}"
        message = command_failure("git branch -d failed", result)
        append_merge_log(plan_dir, f"Held local branch `{branch}` after merge: {message}.")
        return False, f"branch_close_held: {message}"


def append_merge_log(plan_dir: Path, message: str) -> None:
    log_path = plan_dir / "log.md"
    current = log_path.read_text(encoding="utf-8") if log_path.exists() else "# Log\n"
    log_path.write_text(current.rstrip() + f"\n- {message}\n", encoding="utf-8")


def parse_existing_pr(output: str) -> dict | None:
    try:
        data = json.loads(output)
    except ValueError:
        return None
    if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("url"):
        return data[0]
    return None


def parse_pr_url(value: str) -> str:
    import re

    match = re.search(r"https://github\.com/\S+/pull/\d+", value)
    return match.group(0) if match else ""


def build_remote_pr_body(plan_content: str, branch: str) -> str:
    units = plan_readiness.parse_commit_units(plan_content)
    lines = ["## Summary", "", "Ready PR opened after planned commit units were completed.", "", f"Branch: `{branch}`", "", "## Commit Units", ""]
    lines.extend(f"- Commit {unit.number}: {unit.title}" for unit in units)
    lines.extend(["", "Generated by Codex Flow."])
    return "\n".join(lines)


def is_retryable_remote_merge_failure(output: str) -> bool:
    lowered = output.lower()
    return "out of date" in lowered or "behind" in lowered or "update branch" in lowered


def clear_matching_pr_lock(repo: str | Path, branch: str) -> bool:
    flow = state.ensure_initialized(repo)
    lock_path = flow.locks / "pr-lock.md"
    if not lock_path.exists():
        return False
    text = lock_path.read_text(encoding="utf-8")
    match = re.search(r"^(?:- )?Branch:\s*(.+)$", text, flags=re.MULTILINE)
    if not match or match.group(1).strip() != branch:
        return False
    lock_path.unlink()
    return True


def read_active_pr_lock(repo: str | Path) -> tuple[Path, str] | None:
    flow = state.ensure_initialized(repo)
    lock_path = flow.locks / "pr-lock.md"
    if not lock_path.exists():
        return None
    text = lock_path.read_text(encoding="utf-8")
    match = re.search(r"^(?:- )?Branch:\s*(.+)$", text, flags=re.MULTILINE)
    branch = match.group(1).strip() if match else "unknown"
    return lock_path, branch
