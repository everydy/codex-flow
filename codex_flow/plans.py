from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re

from . import plan_readiness, state
from .git_ops import is_git_repo, prepare_branch
from .planner_agent import PlannerAgent, PlannerAgentInput, TemplatePlannerAgent
from .tickets import Ticket, load_ticket, update_ticket_status


@dataclass(frozen=True)
class Plan:
    slug: str
    directory: Path
    plan_path: Path
    queue_json: Path
    queue_md: Path


@dataclass(frozen=True)
class ActivePlan:
    directory: Path
    plan_path: Path
    queue_json: Path
    queue_data: dict


DEFAULT_UNITS = [
    {
        "id": "unit-001",
        "title": "근거 수집과 범위 잠금",
        "allowed_paths": ["README.md", "docs/**", "scripts/**", "tools/**"],
        "verification": ["관련 파일을 rg/find로 확인", "계획과 범위가 요청과 맞는지 점검"],
        "required_skills": ["요청개선"],
        "optional_skills": ["community-research", "project-wiki-all-in-one"],
        "skill_routing_evidence": "요청 범위와 근거를 잠그는 계획 단위다.",
    },
    {
        "id": "unit-002",
        "title": "좁은 구현 패치",
        "allowed_paths": ["scripts/**", "tools/**", "docs/**"],
        "verification": ["단위 테스트 또는 CLI smoke test 실행"],
        "required_skills": ["mission-completion-harness"],
        "optional_skills": ["디자인올인원", "supabase-runtime-debugger", "env-deploy-audit"],
        "skill_routing_evidence": "선택된 구현 단위를 끝까지 완수해야 한다.",
    },
    {
        "id": "unit-003",
        "title": "검증과 리뷰 산출물 정리",
        "allowed_paths": ["docs/**", ".codex-flow/**"],
        "verification": ["diff check", "최종 브리프와 남은 리스크 확인"],
        "required_skills": ["review-all-in-one", "qa-gate"],
        "optional_skills": ["checkpoint", "session-close"],
        "skill_routing_evidence": "검증, 리뷰, handoff 산출물이 완료 조건이다.",
    },
]

DEFAULT_FINAL_GATE = {
    "required_skills": ["review-all-in-one", "qa-gate"],
    "optional_skills": ["checkpoint", "session-close"],
    "skill_routing_evidence": "모든 commit unit이 끝난 뒤 PR/merge 전 최종 점검을 수행한다.",
}


def unique_slug(base: str, plans_dir: Path) -> str:
    slug = state.slugify(base, fallback="plan")
    candidate = slug
    index = 2
    while (plans_dir / candidate).exists():
        candidate = f"{slug}-{index}"
        index += 1
    return candidate


def create_plan_from_ticket(
    ticket_path: str | Path,
    repo: str | Path | None = None,
    branch_name: str | None = None,
    plan_title: str | None = None,
    planner: PlannerAgent | None = None,
    prepare_git_branch: bool = False,
    reason: str = "No PR lock or selected active plan; created a new plan.",
) -> Plan:
    flow = state.ensure_initialized(repo)
    ticket = load_ticket(ticket_path)
    title = plan_title or ticket.title
    slug = unique_slug(title, flow.plans)
    branch = branch_name or f"codex/{slug}"
    if prepare_git_branch and is_git_repo(flow.repo):
        prepare_branch(flow.repo, branch)
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
        "plan_title": title,
        "plan_slug": slug,
        "branch": branch,
        "created_at": state.timestamp(),
        "units": units,
        "final_gate": dict(DEFAULT_FINAL_GATE),
    }

    plan = Plan(
        slug=slug,
        directory=plan_dir,
        plan_path=plan_dir / "plan.md",
        queue_json=plan_dir / "queue.json",
        queue_md=plan_dir / "queue.md",
    )
    write_plan_files(plan, ticket, queue_data)
    selected_planner = planner or TemplatePlannerAgent()
    selected_planner.write_plan(
        PlannerAgentInput(
            repo=flow.repo,
            branch_name=branch,
            plan_title=title,
            plan_path=plan.plan_path,
            queue_path=plan.queue_md,
            log_path=plan.directory / "log.md",
            prompt=ticket.title,
            reason=reason,
        )
    )
    ensure_plan_skill_routing_manifest(plan.plan_path, queue_data)
    plan_readiness.sync_queue_cache_from_plan(plan.plan_path)
    update_ticket_status(ticket.path, "planned")
    state.refresh_dashboard(flow.repo)
    return plan


def write_plan_files(plan: Plan, ticket: Ticket, queue_data: dict) -> None:
    plan.plan_path.write_text(render_plan_md(ticket, plan, queue_data), encoding="utf-8")
    plan.queue_json.write_text(json.dumps(queue_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan.queue_md.write_text(render_queue_md(queue_data), encoding="utf-8")
    (plan.directory / "requests.md").write_text("# Follow-up Requests\n\n- None yet.\n", encoding="utf-8")
    (plan.directory / "log.md").write_text(f"# Log\n\n- {state.timestamp()} plan created\n", encoding="utf-8")
    (plan.directory / "decisions.md").write_text(
        "# Decisions\n\n- Default: remote PR and merge run only through finalize commands after readiness.\n",
        encoding="utf-8",
    )
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


def render_plan_md(ticket: Ticket, plan: Plan, queue_data: dict | None = None) -> str:
    queue_data = queue_data or {}
    branch = queue_data.get("branch") or f"codex/{plan.slug}"
    title = queue_data.get("plan_title") or ticket.title
    units = queue_data.get("units") or DEFAULT_UNITS
    commit_sections: list[str] = []
    for index, unit in enumerate(units, start=1):
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
            f"# Codex Flow Plan: {title}",
            "",
            f"Branch: {branch}",
            f"Title: {title}",
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
            "- Keep remote PR creation and merge inside finalize commands, not inside commit-unit implementation.",
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
            "- Update review artifacts before finalization.",
            "",
            "## Skill Routing Manifest",
            "",
            "Codex Flow does not duplicate specialist skills. This manifest tells each fresh Codex session which skills to load for each phase.",
            "",
            *render_skill_routing_manifest(units, queue_data.get("final_gate") or DEFAULT_FINAL_GATE),
            "",
            "## Commit Units",
            "",
            *commit_sections,
        ]
    )


def render_skill_routing_manifest(units: list[dict], final_gate: dict) -> list[str]:
    lines = [
        "| Phase | Required skills | Optional skills | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for index, unit in enumerate(units, start=1):
        lines.append(
            " | ".join(
                [
                    f"| Commit {index}: {unit['title']}",
                    render_skills_cell(unit.get("required_skills", [])),
                    render_skills_cell(unit.get("optional_skills", [])),
                    unit.get("skill_routing_evidence") or "-",
                ]
            )
            + " |"
        )
    lines.append(
        " | ".join(
            [
                "| Final Gate",
                render_skills_cell(final_gate.get("required_skills", [])),
                render_skills_cell(final_gate.get("optional_skills", [])),
                final_gate.get("skill_routing_evidence") or "-",
            ]
        )
        + " |"
    )
    return lines


def render_skills_cell(skills: list[str]) -> str:
    return ", ".join(f"`{skill}`" for skill in skills) if skills else "-"


def ensure_plan_skill_routing_manifest(plan_path: Path, queue_data: dict) -> bool:
    content = plan_path.read_text(encoding="utf-8")
    if re.search(r"^##\s+Skill Routing Manifest\s*$", content, flags=re.MULTILINE):
        return False
    units = manifest_units_from_plan_content(content, queue_data)
    final_gate = queue_data.get("final_gate") or DEFAULT_FINAL_GATE
    section = "\n".join(
        [
            "## Skill Routing Manifest",
            "",
            "Codex Flow does not duplicate specialist skills. This manifest tells each fresh Codex session which skills to load for each phase.",
            "",
            *render_skill_routing_manifest(units, final_gate),
        ]
    )
    commit_units = re.search(r"^##\s+Commit Units\s*$", content, flags=re.MULTILINE)
    if commit_units:
        repaired = (
            content[: commit_units.start()].rstrip()
            + "\n\n"
            + section
            + "\n\n"
            + content[commit_units.start() :].lstrip()
        )
    else:
        repaired = content.rstrip() + "\n\n" + section + "\n"
    plan_path.write_text(repaired, encoding="utf-8")
    return True


def manifest_units_from_plan_content(plan_content: str, queue_data: dict) -> list[dict]:
    queue_units = queue_data.get("units") or DEFAULT_UNITS
    queue_by_id = {unit.get("id"): unit for unit in queue_units if isinstance(unit, dict)}
    commit_units = plan_readiness.parse_commit_units(plan_content)
    if not commit_units:
        return queue_units
    manifest_units: list[dict] = []
    for commit_unit in commit_units:
        queue_unit = dict(queue_by_id.get(commit_unit.unit_id) or {})
        if not queue_unit and commit_unit.number <= len(queue_units):
            queue_unit = dict(queue_units[commit_unit.number - 1])
        queue_unit.setdefault("id", commit_unit.unit_id)
        queue_unit["title"] = commit_unit.title
        manifest_units.append(queue_unit)
    return manifest_units


def render_queue_md(queue_data: dict) -> str:
    return plan_readiness.render_queue_md(queue_data)


def load_queue(plan_path: str | Path) -> tuple[Path, dict]:
    plan = Path(plan_path).expanduser().resolve()
    plan_dir = plan.parent if plan.name == "plan.md" else plan
    queue_json = plan_dir / "queue.json"
    if not queue_json.exists():
        if (plan_dir / "plan.md").exists():
            return plan_dir, plan_readiness.sync_queue_cache_from_plan(plan_dir / "plan.md")
        raise SystemExit(f"Missing queue: {queue_json}")
    return plan_dir, json.loads(queue_json.read_text(encoding="utf-8"))


def save_queue(plan_dir: Path, queue_data: dict) -> None:
    queue_data["updated_at"] = state.timestamp()
    (plan_dir / "queue.json").write_text(json.dumps(queue_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (plan_dir / "queue.md").write_text(render_queue_md(queue_data), encoding="utf-8")


def append_plan_request(plan_path: str | Path, request: str, reason: str = "Routed to existing plan.") -> Path:
    plan_dir, queue_data = load_queue(plan_path)
    requests_path = plan_dir / "requests.md"
    current = requests_path.read_text(encoding="utf-8") if requests_path.exists() else "# Follow-up Requests\n"
    if "- None yet." in current:
        current = current.replace("- None yet.\n", "")
    requests_path.write_text(
        current.rstrip()
        + "\n"
        + f"- {state.timestamp()} {request}\n"
        + f"  - Reason: {reason}\n",
        encoding="utf-8",
    )
    queue_data.setdefault("requests", []).append({"request": request, "reason": reason, "created_at": state.timestamp()})
    save_queue(plan_dir, queue_data)
    return requests_path


def list_active_plans(repo: str | Path | None = None) -> list[ActivePlan]:
    flow = state.ensure_initialized(repo)
    active: list[ActivePlan] = []
    for plan_path in sorted(flow.plans.glob("*/plan.md")):
        queue_json = plan_path.parent / "queue.json"
        if not queue_json.exists():
            plan_readiness.sync_queue_cache_from_plan(plan_path)
        try:
            queue_data = json.loads(queue_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if any(unit.get("status") != "done" for unit in queue_data.get("units", [])):
            active.append(
                ActivePlan(
                    directory=queue_json.parent,
                    plan_path=queue_json.parent / "plan.md",
                    queue_json=queue_json,
                    queue_data=queue_data,
                )
            )
    return active


def latest_plan(repo: str | Path | None = None) -> ActivePlan | None:
    flow = state.ensure_initialized(repo)
    plan_paths = sorted(flow.plans.glob("*/plan.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    for plan_path in plan_paths:
        queue_json = plan_path.parent / "queue.json"
        if not queue_json.exists():
            plan_readiness.sync_queue_cache_from_plan(plan_path)
        try:
            queue_data = json.loads(queue_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        return ActivePlan(queue_json.parent, queue_json.parent / "plan.md", queue_json, queue_data)
    return None


def choose_active_plan(request: str, repo: str | Path | None = None) -> ActivePlan | None:
    active = list_active_plans(repo)
    if len(active) == 1:
        return active[0]
    if not active:
        return None
    request_terms = set(state.slugify(request, fallback="request").split("-"))
    best: tuple[int, ActivePlan] | None = None
    for plan in active:
        title = plan.queue_data.get("plan_title") or plan.queue_data.get("ticket_title") or plan.directory.name
        plan_terms = set(state.slugify(title, fallback="plan").split("-"))
        score = len(request_terms & plan_terms)
        if score and (best is None or score > best[0]):
            best = (score, plan)
    return best[1] if best else None


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
