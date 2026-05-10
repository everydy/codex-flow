from __future__ import annotations

from pathlib import Path

from . import plans, pr, state


def iter_plan_dirs(flow: state.FlowPaths) -> list[Path]:
    if not flow.plans.exists():
        return []
    return sorted(path for path in flow.plans.iterdir() if path.is_dir() and (path / "queue.json").exists())


def summarize_queue(plan_dir: Path) -> dict[str, int]:
    _, queue = plans.load_queue(plan_dir)
    counts = {status: 0 for status in state.UNIT_STATUSES}
    for unit in queue.get("units", []):
        status_name = unit.get("status", "")
        if status_name in counts:
            counts[status_name] += 1
    return counts


def write_morning_brief(repo: str | Path | None = None) -> Path:
    flow = state.ensure_initialized(repo)
    brief_path = flow.briefs / f"{state.today()}.md"
    lines = [
        f"# Morning Brief: {state.today()}",
        "",
        f"- Updated: {state.timestamp()}",
        "",
        "## Plans",
        "",
    ]
    plan_dirs = iter_plan_dirs(flow)
    if not plan_dirs:
        lines.append("- No active plans.")
    for plan_dir in plan_dirs:
        counts = summarize_queue(plan_dir)
        lines.append(
            f"- `{plan_dir.name}`: ready {counts['ready']}, prompted {counts['prompted']}, done {counts['done']}, needs_work {counts['needs_work']}, human_gate {counts['human_gate']}"
        )
    lines.extend(
        [
            "",
            "## Recommended Review Order",
            "",
            "1. Read plans with `prompted` or `needs_work` units.",
            "2. Run project-specific verification before accepting any merge.",
            "3. Use `open-pr --dry-run` first; create a real PR only after user approval.",
            "",
        ]
    )
    brief_path.write_text("\n".join(lines), encoding="utf-8")
    return brief_path


def write_review(plan_path: str | Path) -> Path:
    plan_dir, queue = plans.load_queue(plan_path)
    review_path = plan_dir / "review.md"
    lines = [
        f"# Review: {queue.get('ticket_title')}",
        "",
        f"- Updated: {state.timestamp()}",
        "",
        "## Queue Status",
        "",
        "| Unit | Status | Title |",
        "| --- | --- | --- |",
    ]
    for unit in queue.get("units", []):
        lines.append(f"| {unit['id']} | {unit['status']} | {unit['title']} |")
    lines.extend(
        [
            "",
            "## Review Checklist",
            "",
            "- [ ] Diff is limited to the intended paths.",
            "- [ ] Verification command was run or a reason is documented.",
            "- [ ] No unrelated user changes were reverted.",
            "- [ ] Remote PR and merge are still disabled unless explicitly approved.",
            "",
        ]
    )
    review_path.write_text("\n".join(lines), encoding="utf-8")
    return review_path


def write_pr_dry_run(plan_path: str | Path) -> Path:
    return pr.write_pr_dry_run(plan_path)
