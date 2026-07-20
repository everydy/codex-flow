from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import fnmatch
import hashlib
import json
import subprocess
from collections.abc import Mapping


FLOW_PREFIX = ".codex-flow/"


@dataclass(frozen=True)
class ProcessResult:
    args: list[str]
    status: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class GitStatusEntry:
    status: str
    path: str
    raw: str


@dataclass(frozen=True)
class GitStatusSnapshot:
    raw: str
    entries: list[GitStatusEntry]


@dataclass(frozen=True)
class ExecutionWorktreeContext:
    source_repo: Path
    execution_repo: Path
    worktree_path: Path
    plan_id: str
    branch: str
    source_plan_sha256: str = ""
    cleanup_state: str = "active"


def run_process(
    args: list[str],
    cwd: str | Path,
    input_text: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int | None = None,
) -> ProcessResult:
    result = subprocess.run(
        args,
        cwd=Path(cwd),
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        env=env,
        timeout=timeout_seconds,
    )
    return ProcessResult(args=args, status=result.returncode, stdout=result.stdout, stderr=result.stderr)


def is_git_repo(repo: str | Path) -> bool:
    return (Path(repo) / ".git").exists()


def require_git_repo(repo: str | Path) -> Path:
    repo_path = Path(repo).resolve()
    if not is_git_repo(repo_path):
        raise SystemExit(f"Not a git repository: {repo_path}")
    return repo_path


def parse_status(raw: str) -> GitStatusSnapshot:
    if "\0" in raw:
        return parse_status_z(raw)
    entries = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        status = line[:2]
        raw_path = line[3:] if len(line) > 3 else ""
        if " -> " in raw_path:
            raw_path = raw_path.rsplit(" -> ", 1)[1]
        entries.append(GitStatusEntry(status=status, path=unquote_status_path(raw_path), raw=line))
    return GitStatusSnapshot(raw=raw, entries=entries)


def parse_status_z(raw: str) -> GitStatusSnapshot:
    entries: list[GitStatusEntry] = []
    records = raw.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        status = record[:2]
        raw_path = record[3:] if len(record) > 3 else ""
        if "R" in status or "C" in status:
            index += 1
        entries.append(GitStatusEntry(status=status, path=raw_path, raw=f"{status} {raw_path}"))
    normalized = "\n".join(entry.raw for entry in entries)
    return GitStatusSnapshot(raw=normalized, entries=entries)


def unquote_status_path(value: str) -> str:
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except ValueError:
            return value[1:-1]
    return value


def status(repo: str | Path) -> GitStatusSnapshot:
    repo_path = require_git_repo(repo)
    result = run_process(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=repo_path)
    if result.status != 0:
        raise SystemExit(command_failure("git status failed", result))
    return parse_status(result.stdout)


def head_summary(repo: str | Path) -> str | None:
    result = run_process(["git", "log", "-1", "--format=%h %s"], cwd=require_git_repo(repo))
    if result.status != 0:
        return None
    return result.stdout.strip() or None


def current_branch(repo: str | Path) -> str:
    result = run_process(["git", "branch", "--show-current"], cwd=require_git_repo(repo))
    if result.status != 0:
        raise SystemExit(command_failure("git branch failed", result))
    return result.stdout.strip()


def prepare_branch(repo: str | Path, branch_name: str) -> None:
    repo_path = require_git_repo(repo)
    trimmed = branch_name.strip()
    if not trimmed:
        raise SystemExit("branch name is required")
    existing = run_process(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{trimmed}"], cwd=repo_path)
    if existing.status == 0:
        result = run_process(["git", "switch", trimmed], cwd=repo_path)
    else:
        result = run_process(["git", "switch", "-c", trimmed], cwd=repo_path)
    if result.status != 0:
        raise SystemExit(command_failure(f"failed to prepare branch {trimmed}", result))


def prepare_execution_worktree(
    source_repo: str | Path,
    plan_id: str,
    branch: str,
    worktree_root: str | Path | None = None,
    *,
    source_plan_sha256: str = "",
) -> ExecutionWorktreeContext:
    source_path = require_git_repo(source_repo)
    normalized_plan_id = plan_id.strip()
    normalized_branch = branch.strip()
    if not normalized_plan_id:
        raise SystemExit("plan id is required")
    if not normalized_branch:
        raise SystemExit("branch name is required")
    root = (
        Path(worktree_root).expanduser().resolve()
        if worktree_root
        else source_path.parent / f".{source_path.name}-codex-flow-worktrees"
    )
    target = (root / normalized_plan_id).resolve()
    if target == source_path or source_path in target.parents:
        raise SystemExit(f"execution worktree must be outside the source repository: {target}")

    if target.exists():
        if not is_git_repo(target):
            raise SystemExit(f"worktree path exists but is not a git worktree: {target}")
        if current_branch(target) != normalized_branch:
            raise SystemExit(
                f"worktree path uses branch {current_branch(target)}, expected {normalized_branch}: {target}"
            )
        if git_common_dir(target) != git_common_dir(source_path):
            raise SystemExit(f"worktree path belongs to a different git repository: {target}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = run_process(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{normalized_branch}"],
            cwd=source_path,
        )
        args = ["git", "worktree", "add", str(target), normalized_branch]
        if existing.status != 0:
            args = ["git", "worktree", "add", "-b", normalized_branch, str(target), "HEAD"]
        result = run_process(args, cwd=source_path)
        if result.status != 0:
            raise SystemExit(command_failure(f"failed to create worktree {target}", result))

    return ExecutionWorktreeContext(
        source_repo=source_path,
        execution_repo=target,
        worktree_path=target,
        plan_id=normalized_plan_id,
        branch=normalized_branch,
        source_plan_sha256=source_plan_sha256,
    )


def git_common_dir(repo: str | Path) -> Path:
    repo_path = require_git_repo(repo)
    result = run_process(["git", "rev-parse", "--git-common-dir"], cwd=repo_path)
    if result.status != 0:
        raise SystemExit(command_failure("git common-dir failed", result))
    common = Path(result.stdout.strip())
    return (repo_path / common).resolve() if not common.is_absolute() else common.resolve()


def cleanup_execution_worktree(
    context: ExecutionWorktreeContext,
    *,
    target_branch: str,
) -> ExecutionWorktreeContext:
    if context.cleanup_state == "removed":
        return context
    source_repo = require_git_repo(context.source_repo)
    execution_repo = require_git_repo(context.execution_repo)
    if execution_repo == source_repo:
        raise SystemExit("refusing to remove the source repository as an execution worktree")
    dirty = dirty_paths(status(execution_repo), ignore_flow=False)
    if dirty:
        raise SystemExit(f"refusing to remove dirty execution worktree: {', '.join(dirty)}")
    target = target_branch.strip()
    if not target:
        raise SystemExit("cleanup target branch is required")
    if context.branch == target:
        raise SystemExit("refusing to remove an execution worktree for the cleanup target branch")
    branch_head = run_process(["git", "rev-parse", f"refs/heads/{context.branch}"], cwd=source_repo)
    if branch_head.status != 0:
        raise SystemExit(command_failure(f"failed to resolve branch {context.branch}", branch_head))
    merged = run_process(
        ["git", "merge-base", "--is-ancestor", context.branch, target],
        cwd=source_repo,
    )
    if merged.status != 0:
        raise SystemExit(f"refusing to remove execution worktree: branch {context.branch} is not merged into {target}")
    removed = run_process(["git", "worktree", "remove", str(execution_repo)], cwd=source_repo)
    if removed.status != 0:
        raise SystemExit(command_failure(f"failed to remove worktree {execution_repo}", removed))
    deleted = run_process(
        ["git", "update-ref", "-d", f"refs/heads/{context.branch}", branch_head.stdout.strip()],
        cwd=source_repo,
    )
    if deleted.status != 0:
        raise SystemExit(command_failure(f"failed to delete merged branch {context.branch}", deleted))
    return replace(context, cleanup_state="removed")


def dirty_paths(snapshot: GitStatusSnapshot, ignore_flow: bool = True) -> list[str]:
    paths = [entry.path for entry in snapshot.entries]
    if ignore_flow:
        paths = [path for path in paths if not path.startswith(FLOW_PREFIX)]
    return paths


def path_allowed(path: str, allowed_paths: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    for raw_pattern in allowed_paths:
        pattern = raw_pattern.replace("\\", "/").strip()
        if not pattern:
            continue
        if pattern in {"*", "**"}:
            return True
        if pattern.endswith("/**") and normalized.startswith(pattern[:-3].rstrip("/") + "/"):
            return True
        if fnmatch.fnmatch(normalized, pattern):
            return True
        if normalized == pattern or normalized.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


def scoped_status_summary(snapshot: GitStatusSnapshot, allowed_paths: list[str]) -> str:
    if not snapshot.entries:
        return ""
    if not allowed_paths:
        return snapshot.raw
    visible = [entry.raw for entry in snapshot.entries if path_allowed(entry.path, allowed_paths)]
    hidden_count = len(snapshot.entries) - len(visible)
    lines = visible or ["Clean within allowed paths"]
    if hidden_count:
        lines.append(f"... {hidden_count} unrelated dirty path(s) hidden from implementer prompt")
    return "\n".join(lines)


def scoped_diff_digest(repo: str | Path, allowed_paths: list[str]) -> str:
    """Digest actual scoped bytes, not only porcelain path/status metadata."""
    repo_path = require_git_repo(repo)
    snapshot = status(repo_path)
    paths = sorted(
        entry.path
        for entry in snapshot.entries
        if not allowed_paths or path_allowed(entry.path, allowed_paths)
    )
    digest = hashlib.sha256()
    for relative in paths:
        digest.update(relative.encode("utf-8"))
        diff = run_process(["git", "diff", "--binary", "HEAD", "--", relative], cwd=repo_path)
        digest.update(diff.stdout.encode("utf-8"))
        path = repo_path / relative
        if path.is_file() and not diff.stdout:
            digest.update(path.read_bytes())
    return digest.hexdigest() if paths else ""


def out_of_scope_diff_digest(repo: str | Path, allowed_paths: list[str]) -> str:
    repo_path = require_git_repo(repo)
    snapshot = status(repo_path)
    paths = sorted(
        entry.path for entry in snapshot.entries if not path_allowed(entry.path, allowed_paths)
    )
    digest = hashlib.sha256()
    for relative in paths:
        digest.update(relative.encode("utf-8"))
        diff = run_process(["git", "diff", "--binary", "HEAD", "--", relative], cwd=repo_path)
        digest.update(diff.stdout.encode("utf-8"))
        path = repo_path / relative
        if path.is_file() and not diff.stdout:
            digest.update(path.read_bytes())
    return digest.hexdigest() if paths else ""


def stash_paths(repo: str | Path, paths: list[str], message: str) -> str:
    repo_path = require_git_repo(repo)
    if not paths:
        return ""
    result = run_process(["git", "stash", "push", "-u", "-m", message, "--", *paths], cwd=repo_path)
    if result.status != 0:
        raise SystemExit(command_failure("git stash failed", result))
    return result.stdout.strip()


def changed_paths_since(before: GitStatusSnapshot, after: GitStatusSnapshot, ignore_flow: bool = True) -> list[str]:
    before_by_path = {entry.path: entry.raw for entry in before.entries}
    changed = [entry.path for entry in after.entries if before_by_path.get(entry.path) != entry.raw]
    if ignore_flow:
        changed = [path for path in changed if not path.startswith(FLOW_PREFIX)]
    return changed


def commit_paths(repo: str | Path, paths: list[str], message: str) -> str:
    repo_path = require_git_repo(repo)
    if not paths:
        raise SystemExit("No paths to commit")
    add_result = run_process(["git", "add", "--", *paths], cwd=repo_path)
    if add_result.status != 0:
        raise SystemExit(command_failure("git add failed", add_result))
    commit_result = run_process(["git", "commit", "-m", message], cwd=repo_path)
    if commit_result.status != 0:
        raise SystemExit(command_failure("git commit failed", commit_result))
    hash_result = run_process(["git", "rev-parse", "--short", "HEAD"], cwd=repo_path)
    if hash_result.status != 0:
        raise SystemExit(command_failure("git rev-parse failed", hash_result))
    return hash_result.stdout.strip()


def push_branch(repo: str | Path, branch_name: str) -> None:
    result = run_process(["git", "push", "-u", "origin", branch_name], cwd=require_git_repo(repo))
    if result.status != 0:
        raise SystemExit(command_failure(f"git push failed for {branch_name}", result))


def merge_branch(repo: str | Path, source_branch: str, target_branch: str) -> ProcessResult:
    repo_path = require_git_repo(repo)
    switch_result = run_process(["git", "switch", target_branch], cwd=repo_path)
    if switch_result.status != 0:
        return switch_result
    return run_process(["git", "merge", source_branch], cwd=repo_path)


def delete_local_branch(repo: str | Path, branch_name: str) -> ProcessResult:
    return run_process(["git", "branch", "-d", branch_name], cwd=require_git_repo(repo))


def unmerged_paths(repo: str | Path) -> list[str]:
    result = run_process(["git", "diff", "--name-only", "--diff-filter=U"], cwd=require_git_repo(repo))
    if result.status != 0:
        raise SystemExit(command_failure("git diff failed", result))
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def has_pending_merge_commit(repo: str | Path) -> bool:
    result = run_process(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=require_git_repo(repo))
    return result.status == 0


def commit_merge(repo: str | Path) -> ProcessResult:
    return run_process(["git", "commit", "--no-edit"], cwd=require_git_repo(repo))


def fetch_branch(repo: str | Path, branch_name: str) -> ProcessResult:
    return run_process(["git", "fetch", "origin", branch_name], cwd=require_git_repo(repo))


def merge_current_branch(repo: str | Path, source: str) -> ProcessResult:
    return run_process(["git", "merge", source], cwd=require_git_repo(repo))


def command_failure(prefix: str, result: ProcessResult) -> str:
    detail = result.stderr.strip() or result.stdout.strip()
    return f"{prefix}: {detail}" if detail else prefix
