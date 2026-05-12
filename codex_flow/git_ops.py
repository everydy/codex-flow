from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import subprocess


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


def run_process(args: list[str], cwd: str | Path, input_text: str | None = None) -> ProcessResult:
    result = subprocess.run(
        args,
        cwd=Path(cwd),
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
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


def unquote_status_path(value: str) -> str:
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except ValueError:
            return value[1:-1]
    return value


def status(repo: str | Path) -> GitStatusSnapshot:
    repo_path = require_git_repo(repo)
    result = run_process(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo_path)
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


def dirty_paths(snapshot: GitStatusSnapshot, ignore_flow: bool = True) -> list[str]:
    paths = [entry.path for entry in snapshot.entries]
    if ignore_flow:
        paths = [path for path in paths if not path.startswith(FLOW_PREFIX)]
    return paths


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
