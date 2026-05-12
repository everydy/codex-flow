from __future__ import annotations

from pathlib import Path
import json
import re

from . import plans, state
from .git_ops import command_failure, merge_branch, push_branch, run_process


def branch_from_queue(queue_data: dict) -> str:
    return queue_data.get("branch") or f"codex/{queue_data.get('plan_slug', 'codex-flow-plan')}"


def plan_is_complete(queue_data: dict) -> bool:
    return all(unit.get("status") == "done" for unit in queue_data.get("units", []))


def write_pr_dry_run(plan_path: str | Path) -> Path:
    plan_dir, queue = plans.load_queue(plan_path)
    branch = branch_from_queue(queue)
    pr_path = plan_dir / "pr-dry-run.md"
    lines = [
        f"# PR Dry Run: {queue.get('ticket_title')}",
        "",
        "This is not a real GitHub PR.",
        "",
        "## Summary",
        "",
        f"- Plan: `{plan_dir}`",
        f"- Ticket: {queue.get('ticket_id')}",
        f"- Branch: `{branch}`",
        f"- Ready: {str(plan_is_complete(queue)).lower()}",
        "",
        "## Unit Status",
        "",
        "| Unit | Status | Title |",
        "| --- | --- | --- |",
    ]
    for unit in queue.get("units", []):
        lines.append(f"| {unit['id']} | {unit['status']} | {unit['title']} |")
    lines.extend(
        [
            "",
            "## Merge Gate",
            "",
            "- Real remote PR creation requires explicit command execution.",
            "- Real merge requires explicit command execution.",
            "",
        ]
    )
    pr_path.write_text("\n".join(lines), encoding="utf-8")
    return pr_path


def create_remote_pr(plan_path: str | Path, draft: bool = True) -> tuple[str, Path]:
    plan_dir, queue = plans.load_queue(plan_path)
    if not plan_is_complete(queue):
        raise SystemExit("Plan is not complete; remote PR creation stopped.")
    repo = plan_dir.parents[2]
    branch = branch_from_queue(queue)
    push_branch(repo, branch)
    args = [
        "gh",
        "pr",
        "create",
        "--head",
        branch,
        "--title",
        queue.get("ticket_title", branch),
        "--body",
        build_pr_body(plan_dir, queue),
    ]
    if draft:
        args.insert(3, "--draft")
    result = run_process(args, cwd=repo)
    if result.status != 0:
        raise SystemExit(command_failure("gh pr create failed", result))
    url = parse_pr_url(result.stdout + "\n" + result.stderr)
    if not url:
        raise SystemExit("Could not parse PR URL from gh output")
    lock_path = write_pr_lock(repo, branch, url, "reviewing")
    return url, lock_path


def write_pr_lock(repo: str | Path, branch: str, url: str, status_name: str) -> Path:
    flow = state.ensure_initialized(repo)
    lock_path = flow.locks / "pr-lock.md"
    lock_path.write_text(
        "\n".join(
            [
                "# PR Lock",
                "",
                f"- Updated: {state.timestamp()}",
                f"- Branch: {branch}",
                f"- URL: {url}",
                f"- Status: {status_name}",
                "- Reason: Draft PR is open for review.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return lock_path


def read_pr_lock(repo: str | Path) -> Path | None:
    flow = state.ensure_initialized(repo)
    lock_path = flow.locks / "pr-lock.md"
    return lock_path if lock_path.exists() else None


def clear_pr_lock(repo: str | Path) -> bool:
    lock_path = read_pr_lock(repo)
    if not lock_path:
        return False
    lock_path.unlink()
    return True


def parse_pr_lock(lock_path: str | Path) -> dict[str, str]:
    text = Path(lock_path).read_text(encoding="utf-8")
    data: dict[str, str] = {}
    for key in ("Branch", "URL", "Status", "Reason"):
        match = re.search(rf"^- {key}:\s*(.+)$", text, flags=re.MULTILINE)
        if match:
            data[key.lower()] = match.group(1).strip()
    return data


def check_pr_lock(repo: str | Path, gh_command: str = "gh", auto_drain: bool = True) -> str:
    lock_path = read_pr_lock(repo)
    if not lock_path:
        return "pr_lock: none"
    lock = parse_pr_lock(lock_path)
    url = lock.get("url")
    if not url:
        return f"pr_lock: active {lock_path}"
    result = run_process([gh_command, "pr", "view", url, "--json", "state,mergedAt,title,url"], cwd=state.resolve_repo(repo))
    if result.status != 0:
        return f"pr_lock: active {lock_path} (status check failed)"
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return f"pr_lock: active {lock_path} (invalid gh output)"
    merged = bool(data.get("mergedAt")) or str(data.get("state", "")).upper() == "MERGED"
    if not merged:
        return f"pr_lock: active {url} ({str(data.get('state', 'open')).lower()})"
    clear_pr_lock(repo)
    if auto_drain:
        return f"pr_lock: merged {url}; cleared; {drain_inbox(repo)}"
    return f"pr_lock: merged {url}; cleared"


def append_lock_resolution(repo: str | Path, message: str) -> Path:
    flow = state.ensure_initialized(repo)
    log_path = flow.logs / "pr-lock-resolution.md"
    current = log_path.read_text(encoding="utf-8") if log_path.exists() else "# PR Lock Resolution Log\n"
    log_path.write_text(current.rstrip() + f"\n- {state.timestamp()} {message}\n", encoding="utf-8")
    return log_path


def drain_inbox(repo: str | Path) -> str:
    from . import tickets

    if read_pr_lock(repo):
        return "drain: locked"
    ticket = tickets.first_inbox_ticket(repo)
    if not ticket:
        return "drain: empty"
    plan = plans.create_plan_from_ticket(ticket.path, repo=repo)
    tickets.update_ticket_status(ticket.path, "planned")
    return f"drain: planned {plan.plan_path}"


def merge_plan(plan_path: str | Path, target: str = "main", remote: bool = False, execute: bool = False) -> str:
    plan_dir, queue = plans.load_queue(plan_path)
    branch = branch_from_queue(queue)
    if not plan_is_complete(queue):
        return "merge: needs_work plan is not complete"
    if not execute:
        return "merge: hard-stop use --execute to run an actual merge"
    repo = plan_dir.parents[2]
    if remote:
        result = run_process(["gh", "pr", "merge", "--merge", branch], cwd=repo)
        if result.status != 0:
            raise SystemExit(command_failure("gh pr merge failed", result))
        return "merge: remote merged"
    result = merge_branch(repo, branch, target)
    if result.status != 0:
        raise SystemExit(command_failure("git merge failed", result))
    return f"merge: local merged {branch} into {target}"


def build_pr_body(plan_dir: Path, queue: dict) -> str:
    unit_lines = "\n".join(f"- {unit['id']}: {unit['status']} - {unit['title']}" for unit in queue.get("units", []))
    return "\n".join(
        [
            f"Plan: `{plan_dir}`",
            "",
            "Unit status:",
            unit_lines,
            "",
            "Generated by Codex Flow.",
        ]
    )


def parse_pr_url(value: str) -> str:
    match = re.search(r"https://github\.com/\S+/pull/\d+", value)
    return match.group(0) if match else ""
