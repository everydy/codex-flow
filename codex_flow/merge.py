from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
import shutil

from . import plan_readiness, state
from .git_ops import (
    branch_is_ancestor,
    command_failure,
    commit_merge,
    delete_local_branch,
    dirty_paths,
    fetch_branch,
    has_pending_merge_commit,
    merge_branch,
    merge_current_branch,
    prune_worktrees,
    push_branch,
    remove_worktree,
    run_process,
    status,
    unmerged_paths,
)
from .merge_agent import CodexMergeAgent, MergeAgent, MergeAgentInput


@dataclass(frozen=True)
class MergeResult:
    action: str
    message: str
    source_branch: str = ""
    target_branch: str = ""
    pr_url: str = ""


@dataclass(frozen=True)
class CleanupResult:
    status: str
    message: str
    archive_path: Path | None = None


class MergeRunner:
    def __init__(self, agent: MergeAgent | None = None, gh_command: str = "gh") -> None:
        self.agent = agent or CodexMergeAgent()
        self.gh_command = gh_command

    def merge_local(
        self,
        plan_path: str | Path,
        target: str = "main",
        execute: bool = False,
        cleanup: bool = True,
    ) -> MergeResult:
        plan_dir, plan_content, log_content = plan_readiness.read_plan_file(plan_path)
        readiness = plan_readiness.check_plan_ready(plan_content, log_content)
        branch = plan_readiness.branch_name_from_plan(plan_content, f"codex/{plan_dir.name}")
        if not readiness.ready:
            return MergeResult("needs_work", "merge: needs_work plan is not complete", branch, target)
        if not execute:
            return MergeResult("hard_stop", "merge: hard-stop use --execute to run an actual merge", branch, target)
        task_repo = state.repo_for_plan(plan_dir)
        source_repo = state.source_repo_for_plan(plan_dir)
        dirty = dirty_paths(status(task_repo))
        if dirty:
            return MergeResult("needs_work", f"merge: needs_work dirty worktree: {', '.join(dirty)}", branch, target)
        result = merge_branch(source_repo, branch, target)
        if result.status == 0:
            append_merge_log(plan_dir, f"Merged local branch `{branch}` into `{target}`.")
            if cleanup:
                cleanup_result = cleanup_generated_worktree(plan_dir, branch, target)
                if cleanup_result.status != "branch_closed":
                    return MergeResult("cleanup_held", f"merge: cleanup_held {cleanup_result.message}", branch, target)
                return MergeResult("branch_closed", f"merge: local merged {branch} into {target}; cleanup: {cleanup_result.message}", branch, target)
            return MergeResult("merged_local", f"merge: local merged {branch} into {target}", branch, target)
        resolved = self.resolve_conflict(source_repo, plan_dir / "plan.md", branch, target, "local", command_failure("git merge failed", result))
        if resolved.action == "merge_needs_work":
            append_merge_log(plan_dir, resolved.message)
            return resolved
        append_merge_log(plan_dir, resolved.message)
        return MergeResult("merged_local", f"merge: local merged {branch} into {target}", branch, target)

    def merge_remote(self, plan_path: str | Path, target: str = "main", execute: bool = False) -> MergeResult:
        plan_dir, plan_content, log_content = plan_readiness.read_plan_file(plan_path)
        readiness = plan_readiness.check_plan_ready(plan_content, log_content)
        branch = plan_readiness.branch_name_from_plan(plan_content, f"codex/{plan_dir.name}")
        if not readiness.ready:
            return MergeResult("needs_work", "merge: needs_work plan is not complete", branch, target)
        if not execute:
            return MergeResult("hard_stop", "merge: hard-stop use --execute to run an actual merge", branch, target)
        repo = state.repo_for_plan(plan_dir)
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
        suffix = "; pr_lock_cleared=true" if lock_cleared else ""
        return MergeResult("merged_remote", f"merge: remote merged {pr_url}{suffix}", branch, target, pr_url)

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


def append_merge_log(plan_dir: Path, message: str) -> None:
    log_path = plan_dir / "log.md"
    current = log_path.read_text(encoding="utf-8") if log_path.exists() else "# Log\n"
    log_path.write_text(current.rstrip() + f"\n- {message}\n", encoding="utf-8")


def cleanup_generated_worktree(plan_dir: Path, branch: str, target: str) -> CleanupResult:
    task_repo = state.repo_for_plan(plan_dir)
    source_repo = state.source_repo_for_plan(plan_dir)
    dirty = dirty_paths(status(task_repo), ignore_flow=True)
    if dirty:
        return hold_cleanup(plan_dir, f"dirty paths: {', '.join(dirty)}")
    if not branch_is_ancestor(source_repo, branch, target):
        return hold_cleanup(plan_dir, f"branch {branch} is not contained in {target}")
    archive_path = archive_flow_state(plan_dir)
    if not archive_path.exists():
        return hold_cleanup(plan_dir, "archive verification failed")
    shutil.rmtree(task_repo / state.FLOW_DIR)
    remove = remove_worktree(source_repo, task_repo)
    if remove.status != 0:
        return hold_cleanup(plan_dir, command_failure("git worktree remove failed", remove), archive_path)
    delete = delete_local_branch(source_repo, branch)
    if delete.status != 0:
        return CleanupResult("held", command_failure("git branch -d failed", delete), archive_path)
    prune_worktrees(source_repo)
    return CleanupResult("branch_closed", f"archived={archive_path}", archive_path)


def archive_flow_state(plan_dir: Path) -> Path:
    task_repo = state.repo_for_plan(plan_dir)
    source_repo = state.source_repo_for_plan(plan_dir)
    slug = plan_dir.name
    worktree_path = Path(state.read_plan_metadata(plan_dir).get("worktree_path", task_repo)).expanduser().resolve()
    archive_root = worktree_path.parent / "_archive" if worktree_path.parent.name else Path.home() / ".config" / "superpowers" / "worktrees" / source_repo.name / "_archive"
    archive_path = archive_root / slug
    if archive_path.exists():
        shutil.rmtree(archive_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    state.write_plan_metadata(plan_dir, {"archive_path": str(archive_path), "cleanup_status": "archived"})
    shutil.copytree(task_repo / state.FLOW_DIR, archive_path / state.FLOW_DIR)
    return archive_path


def hold_cleanup(plan_dir: Path, reason: str, archive_path: Path | None = None) -> CleanupResult:
    try:
        state.write_plan_metadata(plan_dir, {"cleanup_status": "held", "cleanup_reason": reason})
    except OSError:
        pass
    append_merge_log(plan_dir, f"cleanup_held: {reason}")
    return CleanupResult("held", reason, archive_path)


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
