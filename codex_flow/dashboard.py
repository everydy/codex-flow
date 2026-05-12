from __future__ import annotations

from pathlib import Path

from . import plans, pr, state
from .git_ops import dirty_paths, status
from .tickets import list_tickets


def render_dashboard(repo: str | Path | None = None) -> str:
    flow = state.ensure_initialized(repo)
    summary = state.dashboard_summary(flow.repo)
    pr_lock = pr.read_pr_lock(flow.repo)
    dirty = dirty_paths(status(flow.repo)) if (flow.repo / ".git").exists() else []
    inbox_count = len([ticket for ticket in list_tickets(flow.repo) if ticket.status == "inbox"])
    active_plans = plans.list_active_plans(flow.repo)

    lines = [
        "# Codex Flow Dashboard",
        "",
        f"State: {flow.root}",
        f"PR lock: {'active - ' + str(pr_lock) if pr_lock else 'none'}",
        f"Inbox requests: {inbox_count}",
        f"Dirty files: {len(dirty)}",
        f"Plans: {summary['plans']}",
        f"Ready units: {summary['ready_units']}",
        f"Done units: {summary['done_units']}",
        f"Needs work units: {summary['needs_work_units']}",
        "",
    ]
    if dirty:
        lines.extend(["## Dirty Files", "", *[f"- {path}" for path in dirty], ""])
    if not active_plans:
        lines.extend(["## Active Plans", "", "- None", ""])
    else:
        lines.extend(["## Active Plans", ""])
        for active in active_plans:
            units = active.queue_data.get("units", [])
            done = len([unit for unit in units if unit.get("status") == "done"])
            ready = len([unit for unit in units if unit.get("status") == "ready"])
            needs_work = len([unit for unit in units if unit.get("status") == "needs_work"])
            branch = active.queue_data.get("branch", "-")
            title = active.queue_data.get("plan_title") or active.queue_data.get("ticket_title") or active.directory.name
            rel_plan = state.relative_to_repo(flow, active.plan_path)
            lines.extend(
                [
                    f"### {title}",
                    "",
                    f"- Plan: `{rel_plan}`",
                    f"- Branch: `{branch}`",
                    f"- Progress: {done}/{len(units)} done, {ready} ready, {needs_work} needs_work",
                    f"- Suggested command: `python3 scripts/codex_flow.py run-all --plan {rel_plan} --auto-resolve --execute --commit`",
                    "",
                ]
            )
            recent_log = recent_log_lines(active.directory / "log.md")
            if recent_log:
                lines.extend(["Recent log:", *[f"- {line}" for line in recent_log], ""])
    return "\n".join(lines).rstrip()


def recent_log_lines(log_path: Path, limit: int = 3) -> list[str]:
    if not log_path.exists():
        return []
    lines = [line.strip("- ").strip() for line in log_path.read_text(encoding="utf-8").splitlines() if line.startswith("- ")]
    return lines[-limit:]
