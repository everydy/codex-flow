from __future__ import annotations

from pathlib import Path
import json
import re

from . import inbox, plan_readiness, plans, state
from .git_ops import command_failure, merge_branch, push_branch, run_process
from .merge import MergeRunner


class ActivePrLockError(RuntimeError):
    def __init__(self, lock_path: str | Path) -> None:
        self.lock_path = Path(lock_path)
        self.lock = parse_pr_lock(self.lock_path)
        super().__init__(format_active_pr_lock(self.lock_path, self.lock))


def branch_from_queue(queue_data: dict) -> str:
    return queue_data.get("branch") or f"codex/{queue_data.get('plan_slug', 'codex-flow-plan')}"


def plan_is_complete(queue_data: dict, plan_path: str | Path | None = None) -> bool:
    if plan_path:
        plan_dir, plan_content, log_content = plan_readiness.read_plan_file(plan_path)
        return plan_readiness.check_plan_ready(plan_content, log_content).ready
    return all(unit.get("status") == "done" for unit in queue_data.get("units", []))


def write_pr_dry_run(plan_path: str | Path) -> Path:
    plan_dir, queue = plans.load_queue(plan_path)
    queue = plan_readiness.sync_queue_cache_from_plan(plan_dir / "plan.md")
    plan_content = (plan_dir / "plan.md").read_text(encoding="utf-8")
    skill_manifest = plan_readiness.parse_skill_routing_manifest(plan_content)
    branch = branch_from_queue(queue)
    ready = plan_is_complete(queue, plan_dir / "plan.md")
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
        f"- Ready: {str(ready).lower()}",
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
            "## Skill Routing Manifest",
            "",
            "| Phase | Required skills | Optional skills |",
            "| --- | --- | --- |",
        ]
    )
    if skill_manifest:
        for entry in skill_manifest:
            lines.append(f"| {entry.phase} | {plan_readiness.format_skill_list(entry.required_skills)} | {plan_readiness.format_skill_list(entry.optional_skills)} |")
    else:
        lines.append("| - | - | - |")
    lines.extend(
        [
            "",
            "## Merge Gate",
            "",
            "- Remote PR creation is handled by `open-pr --remote` or `create-pr` after readiness.",
            "- Merge is handled by `merge` or `run-all --merge` finalize commands.",
            "",
        ]
    )
    pr_path.write_text("\n".join(lines), encoding="utf-8")
    return pr_path


def create_remote_pr(plan_path: str | Path, draft: bool = True) -> tuple[str, Path]:
    plan_dir, queue = plans.load_queue(plan_path)
    queue = plan_readiness.sync_queue_cache_from_plan(plan_dir / "plan.md")
    if not plan_is_complete(queue, plan_dir / "plan.md"):
        raise SystemExit("Plan is not complete; remote PR creation stopped.")
    repo = plan_dir.parents[2]
    lock_path = read_pr_lock(repo)
    if lock_path:
        raise ActivePrLockError(lock_path)
    branch = branch_from_queue(queue)
    push_branch(repo, branch)
    args = [
        "gh",
        "pr",
        "create",
        "--head",
        branch,
        "--title",
        queue.get("plan_title") or queue.get("ticket_title", branch),
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
    key_map = {"Branch": "branch", "URL": "url", "PR": "url", "Status": "status", "Reason": "reason"}
    for key, target in key_map.items():
        match = re.search(rf"^(?:- )?{key}:\s*(.+)$", text, flags=re.MULTILINE)
        if match:
            data[target] = match.group(1).strip()
    return data


def format_active_pr_lock(lock_path: str | Path, lock: dict[str, str] | None = None) -> str:
    data = lock if lock is not None else parse_pr_lock(lock_path)
    branch = data.get("branch", "unknown")
    url = data.get("url", str(lock_path))
    return f"pr_locked: active branch={branch} url={url} lock={lock_path}"


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
    from . import source_plan

    if read_pr_lock(repo):
        return "drain: locked"
    flow = state.ensure_initialized(repo)

    class SourcePlanRequiredError(Exception):
        pass

    def route_structured(request: inbox.QueuedRequest) -> str:
        try:
            source = source_plan.resolve_source_plan(request.prompt, repo=repo)
        except SystemExit:
            raise SourcePlanRequiredError from None
        plan = plans.create_plan_from_source(source, repo=repo)
        return str(plan.plan_path)

    structured = inbox.read_inbox_requests(flow.inbox)
    if structured:
        try:
            result = inbox.drain_inbox_requests(flow.inbox, lambda: read_pr_lock(repo) is not None, route_structured)
        except SourcePlanRequiredError:
            return "drain: source plan required; queued request left in inbox"
        return result.message
    return "drain: empty"


def merge_plan(plan_path: str | Path, target: str = "main", remote: bool = False, execute: bool = False) -> str:
    runner = MergeRunner()
    result = runner.merge_remote(plan_path, target=target, execute=execute) if remote else runner.merge_local(plan_path, target=target, execute=execute)
    return result.message


def build_pr_body(plan_dir: Path, queue: dict) -> str:
    unit_lines = "\n".join(f"- {unit['id']}: {unit['status']} - {unit['title']}" for unit in queue.get("units", []))
    plan_path = plan_dir / "plan.md"
    skill_manifest = plan_readiness.parse_skill_routing_manifest(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else []
    skill_lines = "\n".join(
        f"- {entry.phase}: required {plan_readiness.format_skill_list(entry.required_skills)}"
        for entry in skill_manifest
    ) or "- Not specified"
    return "\n".join(
        [
            f"Plan: `{plan_dir}`",
            "",
            "Unit status:",
            unit_lines,
            "",
            "Skill routing:",
            skill_lines,
            "",
            "Generated by Codex Flow.",
        ]
    )


def parse_pr_url(value: str) -> str:
    match = re.search(r"https://github\.com/\S+/pull/\d+", value)
    return match.group(0) if match else ""
