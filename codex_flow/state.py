from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re


FLOW_DIR = ".codex-flow"
DEFAULT_DIRS = ("tickets", "plans", "briefs", "locks", "logs")
UNIT_STATUSES = {
    "ready",
    "prompted",
    "in_progress",
    "done",
    "needs_work",
    "blocked",
    "human_gate",
}


@dataclass(frozen=True)
class FlowPaths:
    repo: Path
    root: Path
    tickets: Path
    plans: Path
    briefs: Path
    locks: Path
    logs: Path
    inbox: Path
    dashboard: Path
    config: Path


def resolve_repo(path: str | Path | None = None) -> Path:
    start = Path(path or ".").expanduser().resolve()
    if start.is_file():
        start = start.parent
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start


def paths(repo: str | Path | None = None) -> FlowPaths:
    repo_path = resolve_repo(repo)
    root = repo_path / FLOW_DIR
    return FlowPaths(
        repo=repo_path,
        root=root,
        tickets=root / "tickets",
        plans=root / "plans",
        briefs=root / "briefs",
        locks=root / "locks",
        logs=root / "logs",
        inbox=root / "inbox.md",
        dashboard=root / "dashboard.md",
        config=root / "config.md",
    )


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def slugify(value: str, fallback: str = "codex-flow-plan") -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9가-힣]+", "-", lowered)
    lowered = re.sub(r"-+", "-", lowered).strip("-")
    return lowered or fallback


def write_if_missing(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def ensure_initialized(repo: str | Path | None = None) -> FlowPaths:
    flow = paths(repo)
    flow.root.mkdir(parents=True, exist_ok=True)
    for name in DEFAULT_DIRS:
        (flow.root / name).mkdir(parents=True, exist_ok=True)

    write_if_missing(
        flow.config,
        "\n".join(
            [
                "# Codex Flow Config",
                "",
                "- remote_pr: disabled",
                "- remote_merge: disabled",
                "- recursive_codex_exec: disabled",
                "- default_max_units: 4",
                "- created_by: codex-flow",
                "",
            ]
        ),
    )
    write_if_missing(
        flow.inbox,
        "\n".join(["# Codex Flow Inbox", "", "| Ticket | Status | Created |", "| --- | --- | --- |", ""]),
    )
    write_if_missing(
        flow.dashboard,
        "\n".join(
            [
                "# Codex Flow Dashboard",
                "",
                "- Tickets: 0",
                "- Plans: 0",
                "- Ready units: 0",
                "- Prompted units: 0",
                "",
            ]
        ),
    )
    return flow


def next_sequence(directory: Path, date_prefix: str | None = None) -> int:
    prefix = date_prefix or today()
    highest = 0
    for path in directory.glob(f"{prefix}-*.md"):
        match = re.match(rf"{re.escape(prefix)}-(\d+)", path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def relative_to_repo(flow: FlowPaths, path: Path) -> str:
    try:
        return str(path.relative_to(flow.repo))
    except ValueError:
        return str(path)


def count_plan_units(flow: FlowPaths) -> dict[str, int]:
    counts = {status: 0 for status in UNIT_STATUSES}
    for queue_path in flow.plans.glob("*/queue.json"):
        try:
            import json

            data = json.loads(queue_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for unit in data.get("units", []):
            status = unit.get("status", "")
            if status in counts:
                counts[status] += 1
    return counts


def dashboard_summary(repo: str | Path | None = None) -> dict[str, int]:
    flow = ensure_initialized(repo)
    unit_counts = count_plan_units(flow)
    return {
        "tickets": len(list(flow.tickets.glob("*.md"))),
        "plans": len([path for path in flow.plans.iterdir() if path.is_dir()]),
        "ready_units": unit_counts["ready"],
        "prompted_units": unit_counts["prompted"],
        "done_units": unit_counts["done"],
        "needs_work_units": unit_counts["needs_work"],
        "human_gate_units": unit_counts["human_gate"],
    }


def refresh_dashboard(repo: str | Path | None = None) -> Path:
    flow = ensure_initialized(repo)
    summary = dashboard_summary(flow.repo)
    content = [
        "# Codex Flow Dashboard",
        "",
        f"- Updated: {timestamp()}",
        f"- Tickets: {summary['tickets']}",
        f"- Plans: {summary['plans']}",
        f"- Ready units: {summary['ready_units']}",
        f"- Prompted units: {summary['prompted_units']}",
        f"- Done units: {summary['done_units']}",
        f"- Needs work units: {summary['needs_work_units']}",
        f"- Human gate units: {summary['human_gate_units']}",
        "",
    ]
    flow.dashboard.write_text("\n".join(content), encoding="utf-8")
    return flow.dashboard


def require_initialized(repo: str | Path | None = None) -> FlowPaths:
    flow = paths(repo)
    if not flow.root.exists():
        raise SystemExit(f"Codex Flow is not initialized: {flow.root}")
    return flow

