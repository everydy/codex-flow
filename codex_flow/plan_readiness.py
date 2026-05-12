from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re

from . import state


@dataclass(frozen=True)
class CommitUnit:
    number: int
    title: str
    content: str

    @property
    def unit_id(self) -> str:
        return f"unit-{self.number:03d}"


@dataclass(frozen=True)
class PlanReadiness:
    ready: bool
    reason: str
    total: int
    completed: set[int]
    next_unit: CommitUnit | None


def parse_commit_units(plan_content: str) -> list[CommitUnit]:
    headings = list(re.finditer(r"^### Commit\s+(\d+):\s*(.+?)\s*$", plan_content, flags=re.MULTILINE))
    units: list[CommitUnit] = []
    for index, match in enumerate(headings):
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(plan_content)
        units.append(
            CommitUnit(
                number=int(match.group(1)),
                title=match.group(2).strip(),
                content=plan_content[start:end].strip(),
            )
        )
    return units


def completed_commit_unit_numbers(log_content: str) -> set[int]:
    completed = {int(match.group(1)) for match in re.finditer(r"Completed commit unit\s+(\d+)\b", log_content, flags=re.IGNORECASE)}
    for match in re.finditer(r"\b(?:done|committed|skipped)\s+unit-(\d{3})\b", log_content, flags=re.IGNORECASE):
        completed.add(int(match.group(1)))
    return completed


def needs_work_commit_unit_numbers(log_content: str) -> set[int]:
    needs_work = {int(match.group(1)) for match in re.finditer(r"Commit unit\s+(\d+)\s+needs_work\b", log_content, flags=re.IGNORECASE)}
    for match in re.finditer(r"\bneeds_work\s+unit-(\d{3})\b", log_content, flags=re.IGNORECASE):
        needs_work.add(int(match.group(1)))
    return needs_work


def branch_name_from_plan(plan_content: str, fallback: str = "") -> str:
    return match_line(plan_content, r"^Branch:\s*(.+?)\s*$") or fallback


def title_from_plan(plan_content: str, fallback: str = "") -> str:
    return match_line(plan_content, r"^Title:\s*(.+?)\s*$") or match_line(plan_content, r"^#\s+(?:Codex Flow Plan:|Plan:)?\s*(.+?)\s*$") or fallback


def check_plan_ready(plan_content: str, log_content: str) -> PlanReadiness:
    units = parse_commit_units(plan_content)
    if not units:
        return PlanReadiness(False, "Plan has no commit units.", 0, set(), None)
    completed = completed_commit_unit_numbers(log_content)
    next_unit = next((unit for unit in units if unit.number not in completed), None)
    if next_unit is None:
        return PlanReadiness(True, "All commit units are complete.", len(units), completed, None)
    return PlanReadiness(False, f"Commit unit {next_unit.number} is not complete.", len(units), completed, next_unit)


def read_plan_file(plan_path: str | Path) -> tuple[Path, str, str]:
    plan = Path(plan_path).expanduser().resolve()
    plan_dir = plan.parent if plan.name == "plan.md" else plan
    plan_file = plan_dir / "plan.md"
    log_file = plan_dir / "log.md"
    if not plan_file.exists():
        raise SystemExit(f"Missing plan: {plan_file}")
    return plan_dir, plan_file.read_text(encoding="utf-8"), log_file.read_text(encoding="utf-8") if log_file.exists() else ""


def sync_queue_cache_from_plan(plan_path: str | Path) -> dict:
    plan_dir, plan_content, log_content = read_plan_file(plan_path)
    queue_json = plan_dir / "queue.json"
    existing = read_json(queue_json)
    units = parse_commit_units(plan_content)
    completed = completed_commit_unit_numbers(log_content)
    needs_work = needs_work_commit_unit_numbers(log_content)
    existing_by_id = {unit.get("id"): unit for unit in existing.get("units", []) if isinstance(unit, dict)}
    queue_units = []
    for unit in units:
        old = dict(existing_by_id.get(unit.unit_id, {}))
        old.update(
            {
                "id": unit.unit_id,
                "number": unit.number,
                "title": unit.title,
                "status": "done" if unit.number in completed else "needs_work" if unit.number in needs_work else old.get("status", "ready"),
                "updated_at": old.get("updated_at") or state.timestamp(),
            }
        )
        if old["status"] not in state.UNIT_STATUSES:
            old["status"] = "ready"
        queue_units.append(old)
    existing.update(
        {
            "plan_title": title_from_plan(plan_content, existing.get("plan_title") or existing.get("ticket_title") or plan_dir.name),
            "ticket_title": existing.get("ticket_title") or title_from_plan(plan_content, plan_dir.name),
            "plan_slug": existing.get("plan_slug") or plan_dir.name,
            "branch": branch_name_from_plan(plan_content, existing.get("branch") or f"codex/{plan_dir.name}"),
            "updated_at": state.timestamp(),
            "units": queue_units,
        }
    )
    queue_json.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (plan_dir / "queue.md").write_text(render_queue_md(existing), encoding="utf-8")
    return existing


def render_queue_md(queue_data: dict) -> str:
    lines = [
        f"# Queue: {queue_data.get('ticket_title') or queue_data.get('plan_title') or 'Codex Flow Plan'}",
        "",
        "| Unit | Status | Title | Prompt |",
        "| --- | --- | --- | --- |",
    ]
    for unit in queue_data.get("units", []):
        prompt = unit.get("prompt_path") or "-"
        lines.append(f"| {unit.get('id')} | {unit.get('status')} | {unit.get('title')} | {prompt} |")
    lines.append("")
    return "\n".join(lines)


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def match_line(content: str, pattern: str) -> str:
    match = re.search(pattern, content, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""
