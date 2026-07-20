from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re

from . import execution_policy, state


@dataclass(frozen=True)
class CommitUnit:
    number: int
    title: str
    content: str

    @property
    def unit_id(self) -> str:
        return f"unit-{self.number:03d}"


@dataclass(frozen=True)
class SkillRoutingEntry:
    phase: str
    required_skills: tuple[str, ...]
    optional_skills: tuple[str, ...]
    evidence: str


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


def parse_skill_routing_manifest(plan_content: str) -> list[SkillRoutingEntry]:
    section = markdown_section(plan_content, "Skill Routing Manifest")
    if not section:
        return []
    entries: list[SkillRoutingEntry] = []
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        phase, required, optional, evidence = cells[:4]
        if phase.lower() == "phase" or _is_separator_row(cells):
            continue
        entries.append(
            SkillRoutingEntry(
                phase=clean_markdown_cell(phase),
                required_skills=parse_skill_cell(required),
                optional_skills=parse_skill_cell(optional),
                evidence=clean_markdown_cell(evidence),
            )
        )
    return entries


def skill_routing_for_commit(plan_content: str, commit_number: int) -> SkillRoutingEntry | None:
    commit_key = f"commit {commit_number}"
    unit_key = f"unit-{commit_number:03d}"
    for entry in parse_skill_routing_manifest(plan_content):
        phase = entry.phase.lower()
        if commit_key in phase or unit_key in phase:
            return entry
    return None


def final_gate_skill_routing(plan_content: str) -> SkillRoutingEntry | None:
    for entry in parse_skill_routing_manifest(plan_content):
        if "final gate" in entry.phase.lower() or "최종" in entry.phase:
            return entry
    return None


def format_skill_routing_entry(entry: SkillRoutingEntry | None) -> str:
    if entry is None:
        return "- Not specified"
    return "\n".join(
        [
            f"- Phase: {entry.phase}",
            f"- Required skills: {format_skill_list(entry.required_skills)}",
            f"- Optional skills: {format_skill_list(entry.optional_skills)}",
            f"- Evidence: {entry.evidence or '-'}",
        ]
    )


def format_skill_list(skills: tuple[str, ...] | list[str]) -> str:
    return ", ".join(f"`{skill}`" for skill in skills) if skills else "-"


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
    skill_entries = {entry.phase.lower(): entry for entry in parse_skill_routing_manifest(plan_content)}
    existing_by_id = {unit.get("id"): unit for unit in existing.get("units", []) if isinstance(unit, dict)}
    queue_units = []
    for unit in units:
        old = dict(existing_by_id.get(unit.unit_id, {}))
        skill_entry = _skill_entry_for_unit(unit, skill_entries)
        old.update(
            {
                "id": unit.unit_id,
                "number": unit.number,
                "title": unit.title,
                "status": "done" if unit.number in completed else "needs_work" if unit.number in needs_work else old.get("status", "ready"),
                "updated_at": old.get("updated_at") or state.timestamp(),
            }
        )
        if skill_entry:
            old["required_skills"] = list(skill_entry.required_skills)
            old["optional_skills"] = list(skill_entry.optional_skills)
            old["skill_routing_evidence"] = skill_entry.evidence
        policy_input = {**old, "content": unit.content}
        old["execution_policy"] = execution_policy.classify_execution_policy(policy_input).to_dict()
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
        "| Unit | Status | Title | Required Skills | Prompt |",
        "| --- | --- | --- | --- | --- |",
    ]
    for unit in queue_data.get("units", []):
        prompt = unit.get("prompt_path") or "-"
        required = ", ".join(f"`{skill}`" for skill in unit.get("required_skills", [])) or "-"
        lines.append(f"| {unit.get('id')} | {unit.get('status')} | {unit.get('title')} | {required} | {prompt} |")
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


def markdown_section(content: str, heading: str) -> str:
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$", content, flags=re.MULTILINE)
    if not match:
        return ""
    start = match.end()
    next_heading = re.search(r"^##\s+", content[start:], flags=re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(content)
    return content[start:end].strip()


def parse_skill_cell(cell: str) -> tuple[str, ...]:
    cleaned = clean_markdown_cell(cell)
    if cleaned in {"", "-", "None", "none", "없음"}:
        return ()
    cleaned = re.sub(r"<br\s*/?>", ",", cleaned, flags=re.IGNORECASE)
    values: list[str] = []
    for part in re.split(r"[,;\n]+", cleaned):
        skill = part.strip().strip("-* ").strip()
        if skill and skill not in {"-", "None", "none", "없음"}:
            values.append(skill)
    return tuple(dict.fromkeys(values))


def clean_markdown_cell(cell: str) -> str:
    value = cell.strip()
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    return value.strip()


def _is_separator_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells if cell.strip())


def _skill_entry_for_unit(unit: CommitUnit, entries: dict[str, SkillRoutingEntry]) -> SkillRoutingEntry | None:
    commit_key = f"commit {unit.number}"
    unit_key = unit.unit_id
    for phase, entry in entries.items():
        if commit_key in phase or unit_key in phase:
            return entry
    return None
