from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from . import state
from .tickets import Ticket, load_ticket


@dataclass(frozen=True)
class Plan:
    slug: str
    directory: Path
    plan_path: Path
    queue_json: Path
    queue_md: Path


DEFAULT_UNITS = [
    {
        "id": "unit-001",
        "title": "근거 수집과 범위 잠금",
        "allowed_paths": ["README.md", "docs/**", "scripts/**", "tools/**"],
        "verification": ["관련 파일을 rg/find로 확인", "계획과 범위가 요청과 맞는지 점검"],
    },
    {
        "id": "unit-002",
        "title": "좁은 구현 패치",
        "allowed_paths": ["scripts/**", "tools/**", "docs/**"],
        "verification": ["단위 테스트 또는 CLI smoke test 실행"],
    },
    {
        "id": "unit-003",
        "title": "검증과 리뷰 산출물 정리",
        "allowed_paths": ["docs/**", ".codex-flow/**"],
        "verification": ["diff check", "최종 브리프와 남은 리스크 확인"],
    },
]


def unique_slug(base: str, plans_dir: Path) -> str:
    slug = state.slugify(base, fallback="plan")
    candidate = slug
    index = 2
    while (plans_dir / candidate).exists():
        candidate = f"{slug}-{index}"
        index += 1
    return candidate


def create_plan_from_ticket(ticket_path: str | Path, repo: str | Path | None = None) -> Plan:
    flow = state.ensure_initialized(repo)
    ticket = load_ticket(ticket_path)
    slug = unique_slug(ticket.title, flow.plans)
    plan_dir = flow.plans / slug
    plan_dir.mkdir(parents=True, exist_ok=False)
    (plan_dir / "prompts").mkdir(parents=True, exist_ok=True)

    units = []
    for item in DEFAULT_UNITS:
        unit = dict(item)
        unit["status"] = "ready"
        unit["prompt_path"] = ""
        unit["updated_at"] = state.timestamp()
        units.append(unit)

    queue_data = {
        "ticket_id": ticket.id,
        "ticket_title": ticket.title,
        "plan_slug": slug,
        "branch": f"codex/{slug}",
        "created_at": state.timestamp(),
        "units": units,
    }

    plan = Plan(
        slug=slug,
        directory=plan_dir,
        plan_path=plan_dir / "plan.md",
        queue_json=plan_dir / "queue.json",
        queue_md=plan_dir / "queue.md",
    )
    write_plan_files(plan, ticket, queue_data)
    state.refresh_dashboard(flow.repo)
    return plan


def write_plan_files(plan: Plan, ticket: Ticket, queue_data: dict) -> None:
    plan.plan_path.write_text(render_plan_md(ticket, plan), encoding="utf-8")
    plan.queue_json.write_text(json.dumps(queue_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan.queue_md.write_text(render_queue_md(queue_data), encoding="utf-8")
    (plan.directory / "log.md").write_text(f"# Log\n\n- {state.timestamp()} plan created\n", encoding="utf-8")
    (plan.directory / "decisions.md").write_text("# Decisions\n\n- Default: remote PR and merge are disabled.\n", encoding="utf-8")
    (plan.directory / "artifacts.md").write_text("# Artifacts\n\n- None yet.\n", encoding="utf-8")
    (plan.directory / "handoff.md").write_text(
        "\n".join(
            [
                "# Handoff",
                "",
                f"- Ticket: {ticket.id}",
                f"- Plan: {plan.plan_path}",
                "- Resume: run `codex_flow.py run-next --plan <plan.md>`.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def render_plan_md(ticket: Ticket, plan: Plan) -> str:
    branch = f"codex/{plan.slug}"
    commit_sections: list[str] = []
    for index, unit in enumerate(DEFAULT_UNITS, start=1):
        verification = "\n".join(f"- {item}" for item in unit["verification"])
        allowed = "\n".join(f"- {item}" for item in unit["allowed_paths"])
        commit_sections.extend(
            [
                f"### Commit {index}: {unit['title']}",
                "",
                "Allowed paths:",
                allowed,
                "",
                "Verification:",
                verification,
                "",
            ]
        )
    return "\n".join(
        [
            f"# Codex Flow Plan: {ticket.title}",
            "",
            f"Branch: {branch}",
            f"Title: {ticket.title}",
            "",
            "## Ticket",
            "",
            f"- ID: {ticket.id}",
            f"- Priority: {ticket.priority}",
            f"- Project: {ticket.project}",
            f"- Source: {ticket.path}",
            "",
            "## Objective",
            "",
            f"- Complete the request: {ticket.title}",
            "",
            "## Scope",
            "",
            "- Use the smallest safe implementation unit.",
            "- Keep remote PR creation and merge disabled unless the user explicitly approves them.",
            "- Do not revert unrelated worktree changes.",
            "",
            "## Queue",
            "",
            "- [Queue](queue.md)",
            "- [Machine state](queue.json)",
            "",
            "## Verification",
            "",
            "- Run the narrowest relevant tests or CLI smoke commands.",
            "- Update review artifacts before asking for user merge approval.",
            "",
            "## Commit Units",
            "",
            *commit_sections,
        ]
    )


def render_queue_md(queue_data: dict) -> str:
    lines = [
        f"# Queue: {queue_data['ticket_title']}",
        "",
        "| Unit | Status | Title | Prompt |",
        "| --- | --- | --- | --- |",
    ]
    for unit in queue_data["units"]:
        prompt = unit.get("prompt_path") or "-"
        lines.append(f"| {unit['id']} | {unit['status']} | {unit['title']} | {prompt} |")
    lines.append("")
    return "\n".join(lines)


def load_queue(plan_path: str | Path) -> tuple[Path, dict]:
    plan = Path(plan_path).expanduser().resolve()
    plan_dir = plan.parent if plan.name == "plan.md" else plan
    queue_json = plan_dir / "queue.json"
    if not queue_json.exists():
        raise SystemExit(f"Missing queue: {queue_json}")
    return plan_dir, json.loads(queue_json.read_text(encoding="utf-8"))


def save_queue(plan_dir: Path, queue_data: dict) -> None:
    queue_data["updated_at"] = state.timestamp()
    (plan_dir / "queue.json").write_text(json.dumps(queue_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (plan_dir / "queue.md").write_text(render_queue_md(queue_data), encoding="utf-8")


def unfinished_units(queue_data: dict) -> list[dict]:
    return [unit for unit in queue_data.get("units", []) if unit.get("status") != "done"]


def requeue_unfinished_units(plan_path: str | Path, reason: str) -> list[dict]:
    plan_dir, queue_data = load_queue(plan_path)
    changed: list[dict] = []
    for unit in unfinished_units(queue_data):
        if unit.get("status") != "ready":
            unit["status"] = "ready"
            unit["updated_at"] = state.timestamp()
            unit["auto_resolve_reason"] = reason
            changed.append(unit)
    if changed:
        save_queue(plan_dir, queue_data)
    return changed


def find_unit(queue_data: dict, unit_id: str) -> dict:
    for unit in queue_data.get("units", []):
        if unit.get("id") == unit_id:
            return unit
    raise SystemExit(f"Unknown unit: {unit_id}")


def mark_unit(plan_path: str | Path, unit_id: str, status_name: str) -> dict:
    if status_name not in state.UNIT_STATUSES:
        raise SystemExit(f"Invalid status: {status_name}")
    plan_dir, queue_data = load_queue(plan_path)
    unit = find_unit(queue_data, unit_id)
    unit["status"] = status_name
    unit["updated_at"] = state.timestamp()
    save_queue(plan_dir, queue_data)
    state.refresh_dashboard(plan_dir.parents[2])
    return unit
