from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re

from . import execution_policy, plan_first_extract, plan_readiness, source_plan, state
from .git_ops import ExecutionWorktreeContext, cleanup_execution_worktree, is_git_repo, prepare_branch, prepare_execution_worktree
from .planner_agent import PlannerAgent, PlannerAgentInput, TemplatePlannerAgent
from .tickets import Ticket, create_internal_ticket, load_ticket, update_ticket_status


PLAN_FIRST_SKILL = "plan-first-implementation"

PLAN_FIRST_TRIGGER_RE = re.compile(
    r"("
    r"구현|패치|기능|화면|레이아웃|디자인|UI|UX|리팩터|리팩토|통합|API|DB|데이터베이스|라우팅|"
    r"frontend|backend|implement|implementation|feature|layout|design|refactor|integration|routing|component|screen|code"
    r")",
    flags=re.IGNORECASE,
)

PLAN_FIRST_EXEMPT_RE = re.compile(
    r"(검증|리뷰|review|qa|final gate|브리핑|brief|상태|status|test-only|테스트만|문서만)",
    flags=re.IGNORECASE,
)

DEFAULT_IMPLEMENTATION_ALLOWED_PATHS = [
    "frontend/**",
    "backend/**",
    "functions/**",
    "src/**",
    "app/**",
    "server/**",
    "tests/**",
    "docs/**",
    "scripts/**",
    "tools/**",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "vite.config.*",
    "tsconfig*.json",
]


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
        "required_skills": ["요청개선", PLAN_FIRST_SKILL],
        "optional_skills": ["community-research", "project-wiki-all-in-one"],
        "skill_routing_evidence": "요청 범위와 구현 전 계획 게이트를 잠그는 단위다.",
    },
    {
        "id": "unit-002",
        "title": "좁은 구현 패치",
        "allowed_paths": DEFAULT_IMPLEMENTATION_ALLOWED_PATHS,
        "verification": ["단위 테스트 또는 CLI smoke test 실행"],
        "required_skills": [PLAN_FIRST_SKILL, "mission-completion-harness"],
        "optional_skills": ["디자인올인원", "supabase-runtime-debugger", "env-deploy-audit"],
        "skill_routing_evidence": "선택된 구현 단위를 끝까지 완수해야 한다.",
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
        "source_repo": str(flow.repo),
        "execution_repo": str(flow.repo),
        "worktree_path": str(flow.repo),
        "source_plan_sha256": source_plan.sha256_text(ticket.path.read_text(encoding="utf-8")),
        "cleanup_state": "not_applicable",
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


def create_plan_from_source(
    source: source_plan.SourcePlan,
    repo: str | Path | None = None,
    branch_name: str | None = None,
    plan_title: str | None = None,
    prepare_git_branch: bool = False,
    worktree_root: str | Path | None = None,
) -> Plan:
    flow = state.ensure_initialized(repo)
    title = plan_title or source.title
    slug = unique_slug(title, flow.plans)
    branch = branch_name or f"codex/{slug}"
    execution_context = ExecutionWorktreeContext(
        source_repo=flow.repo,
        execution_repo=flow.repo,
        worktree_path=flow.repo,
        plan_id=slug,
        branch=branch,
        source_plan_sha256=source.sha256,
        cleanup_state="not_applicable",
    )
    if prepare_git_branch and is_git_repo(flow.repo):
        execution_context = prepare_execution_worktree(
            flow.repo,
            slug,
            branch,
            worktree_root,
            source_plan_sha256=source.sha256,
        )
    plan_dir = flow.plans / slug
    plan_dir.mkdir(parents=True, exist_ok=False)
    (plan_dir / "prompts").mkdir(parents=True, exist_ok=True)
    (plan_dir / "tickets").mkdir(parents=True, exist_ok=True)

    top_level_ticket = create_internal_ticket(
        f"Adopt source plan: {title}",
        repo=flow.repo,
        project="codex-flow",
        no_implement=True,
    )
    extracted = plan_first_extract.extract_tickets(source.content)
    confidence = plan_first_extract.overall_confidence(extracted)
    source_plan.snapshot_source_plan(source, plan_dir, extraction_confidence=confidence)
    plan_first_extract.write_ticket_files(plan_dir / "tickets", extracted)

    queue_data = queue_from_source_tickets(
        source,
        extracted,
        slug,
        title,
        branch,
        top_level_ticket,
        confidence,
        execution_context=execution_context,
    )
    plan = Plan(slug, plan_dir, plan_dir / "plan.md", plan_dir / "queue.json", plan_dir / "queue.md")
    write_source_plan_files(plan, source, extracted, queue_data, top_level_ticket)
    persist_execution_context(plan_dir, execution_context)
    ensure_plan_skill_routing_manifest(plan.plan_path, queue_data)
    plan_readiness.sync_queue_cache_from_plan(plan.plan_path)
    update_ticket_status(top_level_ticket.path, "planned")
    state.refresh_dashboard(flow.repo)
    return plan


def queue_from_source_tickets(
    source: source_plan.SourcePlan,
    extracted: list[plan_first_extract.ExtractedTicket],
    slug: str,
    title: str,
    branch: str,
    top_level_ticket: Ticket,
    extraction_confidence: str,
    execution_context: ExecutionWorktreeContext | None = None,
) -> dict:
    source_ref_path = source_plan.relative_path(source.path, source.repo)
    source_manifest = source_manifest_by_number(source.content)
    units: list[dict] = []
    for index, ticket in enumerate(extracted, start=1):
        template = DEFAULT_UNITS[0] if index == 1 else DEFAULT_UNITS[1]
        path_scope = plan_first_extract.extract_path_scope(ticket.excerpt, source.repo)
        allowed_paths = path_scope.allowed_paths or template.get(
            "allowed_paths",
            DEFAULT_IMPLEMENTATION_ALLOWED_PATHS,
        )
        manifest_entry = source_manifest.get(index)
        status = "human_gate" if ticket.confidence == "low" or path_scope.external_allowed_paths else "ready"
        skill_routing_evidence = f"Derived from source plan section: {ticket.source_section}"
        if manifest_entry:
            skill_routing_evidence = manifest_entry.evidence or skill_routing_evidence
        if path_scope.external_allowed_paths:
            skill_routing_evidence = (
                skill_routing_evidence.rstrip(".")
                + f". External paths require operator review: {', '.join(path_scope.external_allowed_paths)}"
            )
        unit = dict(template)
        unit.update(
            {
                "id": f"unit-{index:03d}",
                "number": index,
                "title": ticket.title,
                "status": status,
                "prompt_path": "",
                "updated_at": state.timestamp(),
                "ticket_id": ticket.id,
                "source_plan_ref": {
                    "path": source_ref_path,
                    "section": ticket.source_section,
                    "excerpt_hash": plan_first_extract.excerpt_hash(ticket.excerpt),
                },
                "extraction_confidence": ticket.confidence,
                "allowed_paths": allowed_paths,
                "external_allowed_paths": path_scope.external_allowed_paths,
                "verification": template.get("verification", ["Narrow CLI or test verification"]),
                "required_skills": list(manifest_entry.required_skills)
                if manifest_entry
                else template.get("required_skills", [PLAN_FIRST_SKILL, "mission-completion-harness"]),
                "optional_skills": list(manifest_entry.optional_skills) if manifest_entry else template.get("optional_skills", []),
                "skill_routing_evidence": skill_routing_evidence,
            }
        )
        unit["execution_policy"] = execution_policy.classify_execution_policy(
            {**unit, "content": ticket.excerpt}
        ).to_dict()
        units.append(unit)
    context = execution_context or ExecutionWorktreeContext(
        source_repo=source.repo,
        execution_repo=source.repo,
        worktree_path=source.repo,
        plan_id=slug,
        branch=branch,
        source_plan_sha256=source.sha256,
        cleanup_state="not_applicable",
    )
    return {
        "ticket_id": top_level_ticket.id,
        "ticket_title": top_level_ticket.title,
        "plan_title": title,
        "plan_slug": slug,
        "branch": branch,
        "created_at": state.timestamp(),
        "route_mode": "plan_first_source",
        **execution_context_metadata(context),
        "source_plan": {
            "path": source_ref_path,
            "title": source.title,
            "sha256": source.sha256,
            "extraction_confidence": extraction_confidence,
        },
        "units": units,
        "final_gate": dict(DEFAULT_FINAL_GATE),
    }


def source_manifest_by_number(source_content: str) -> dict[int, plan_readiness.SkillRoutingEntry]:
    entries: dict[int, plan_readiness.SkillRoutingEntry] = {}
    for entry in plan_readiness.parse_skill_routing_manifest(source_content):
        number = manifest_phase_number(entry.phase)
        if number is not None:
            entries[number] = entry
    return entries


def manifest_phase_number(phase: str) -> int | None:
    match = re.search(r"\b(?:Commit|Phase)\s+(\d+)\b", phase, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def write_plan_files(plan: Plan, ticket: Ticket, queue_data: dict) -> None:
    plan.plan_path.write_text(render_plan_md(ticket, plan, queue_data), encoding="utf-8")
    plan.queue_json.write_text(json.dumps(queue_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan.queue_md.write_text(render_queue_md(queue_data), encoding="utf-8")
    (plan.directory / "requests.md").write_text("# Follow-up Requests\n\n- None yet.\n", encoding="utf-8")
    (plan.directory / "log.md").write_text(f"# Log\n\n- {state.timestamp()} plan created\n", encoding="utf-8")
    (plan.directory / "decisions.md").write_text(
        "# Decisions\n\n- Default: completed run-all locally merges into the target branch and closes the work branch; remote PR and remote merge stay explicit finalize choices.\n",
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
                "- Resume all units and close the branch when complete: run `codex_flow.py run-all --plan <plan.md> --auto-resolve`.",
                "- Single unit repair/manual step: run `codex_flow.py run-next --plan <plan.md> --auto-resolve`.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_source_plan_files(
    plan: Plan,
    source: source_plan.SourcePlan,
    extracted: list[plan_first_extract.ExtractedTicket],
    queue_data: dict,
    top_level_ticket: Ticket,
) -> None:
    plan.plan_path.write_text(render_source_plan_md(source, plan, extracted, queue_data, top_level_ticket), encoding="utf-8")
    (plan.directory / "macro-plan.md").write_text(render_macro_plan_md(source, extracted, queue_data), encoding="utf-8")
    plan.queue_json.write_text(json.dumps(queue_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan.queue_md.write_text(render_queue_md(queue_data), encoding="utf-8")
    (plan.directory / "requests.md").write_text("# Follow-up Requests\n\n- None yet.\n", encoding="utf-8")
    (plan.directory / "log.md").write_text(f"# Log\n\n- {state.timestamp()} source plan adopted\n", encoding="utf-8")
    (plan.directory / "decisions.md").write_text(
        "\n".join(
            [
                "# Decisions",
                "",
                "- Route input is the plan-first source document.",
                "- Original source file is not rewritten during route.",
                "- Source drift stops run-next/run-all unless explicitly accepted.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (plan.directory / "artifacts.md").write_text("# Artifacts\n\n- source-plan.md\n- source.json\n- macro-plan.md\n", encoding="utf-8")
    (plan.directory / "handoff.md").write_text(
        "\n".join(
            [
                "# Handoff",
                "",
                f"- Source plan: {source_plan.relative_path(source.path, source.repo)}",
                f"- Plan: {plan.plan_path}",
                "- Resume all units and close the branch when complete: run `codex_flow.py run-all --plan <plan.md> --auto-resolve`.",
                "- Single unit repair/manual step: run `codex_flow.py run-next --plan <plan.md> --auto-resolve`.",
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


def render_source_plan_md(
    source: source_plan.SourcePlan,
    plan: Plan,
    extracted: list[plan_first_extract.ExtractedTicket],
    queue_data: dict,
    top_level_ticket: Ticket,
) -> str:
    branch = queue_data.get("branch") or f"codex/{plan.slug}"
    title = queue_data.get("plan_title") or source.title
    units = queue_data.get("units") or []
    commit_sections: list[str] = []
    for index, unit in enumerate(units, start=1):
        verification = "\n".join(f"- {item}" for item in unit.get("verification", [])) or "- Not specified"
        allowed = "\n".join(f"- {item}" for item in unit.get("allowed_paths", [])) or "- Not specified"
        external_allowed = "\n".join(f"- {item}" for item in unit.get("external_allowed_paths", [])) or "- None"
        source_ref = unit.get("source_plan_ref") or {}
        commit_sections.extend(
            [
                f"### Commit {index}: {unit['title']}",
                "",
                "Source ticket:",
                f"- Ticket: {unit.get('ticket_id')}",
                f"- Section: {source_ref.get('section', '-')}",
                f"- Excerpt hash: {source_ref.get('excerpt_hash', '-')}",
                "",
                "Allowed paths:",
                allowed,
                "",
                "External allowed paths:",
                external_allowed,
                "",
                "Verification:",
                verification,
                "",
            ]
        )
    ticket_lines = [
        f"- {ticket.id}: {ticket.title} ({ticket.confidence}, {ticket.source_section})"
        for ticket in extracted
    ]
    return "\n".join(
        [
            f"# Codex Flow Plan: {title}",
            "",
            f"Branch: {branch}",
            f"Title: {title}",
            "",
            "## Source Plan",
            "",
            f"- Path: {source_plan.relative_path(source.path, source.repo)}",
            f"- Title: {source.title}",
            f"- SHA256: {source.sha256}",
            "- Snapshot: source-plan.md",
            "- Metadata: source.json",
            "- Macro plan: macro-plan.md",
            "",
            "## Ticket",
            "",
            f"- ID: {top_level_ticket.id}",
            f"- Priority: {top_level_ticket.priority}",
            f"- Project: {top_level_ticket.project}",
            f"- Source: {top_level_ticket.path}",
            "",
            "## Objective",
            "",
            "- Adopt the approved plan-first source document and execute its extracted work units.",
            "",
            "## Extracted Tickets",
            "",
            *ticket_lines,
            "",
            "## Queue",
            "",
            "- [Queue](queue.md)",
            "- [Machine state](queue.json)",
            "- [Source snapshot](source-plan.md)",
            "- [Macro plan](macro-plan.md)",
            "",
            "## Verification",
            "",
            "- Run the narrowest relevant tests or CLI smoke commands.",
            "- Check source drift before run-next/run-all.",
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


def render_macro_plan_md(source: source_plan.SourcePlan, extracted: list[plan_first_extract.ExtractedTicket], queue_data: dict) -> str:
    source_path = source_plan.relative_path(source.path, source.repo)
    lines = [
        f"# Macro Plan: {source.title}",
        "",
        f"- Source plan: `{source_path}`",
        f"- Extraction confidence: {queue_data.get('source_plan', {}).get('extraction_confidence', '-')}",
        "",
        "## Execution Phases",
        "",
    ]
    for index, ticket in enumerate(extracted, start=1):
        lines.extend(
            [
                f"### Phase {index}: {ticket.title}",
                "",
                f"- Ticket: `{ticket.id}`",
                f"- Source section: {ticket.source_section}",
                f"- Confidence: {ticket.confidence}",
                f"- Depends on: {'none' if index == 1 else f'ticket-{index - 1:03d}'}",
                "",
            ]
        )
    lines.extend(
        [
            "## Stop And Revise Conditions",
            "",
            "- Source drift exists and the operator did not pass `--accept-source-drift`.",
            "- Extraction confidence is low and the generated single ticket is too broad; Codex Flow holds it at `human_gate` instead of auto-running it.",
            "- A commit unit review returns needs_work after repair attempts.",
            "",
            "## Final Gate",
            "",
            "- Review all completed units.",
            "- Run the narrowest relevant verification.",
            "- After all units are done, default to local merge into the target branch and close the completed work branch. Use PR/remote finalize only when that path is explicitly requested.",
            "",
        ]
    )
    return "\n".join(lines)


def render_skill_routing_manifest(units: list[dict], final_gate: dict) -> list[str]:
    lines = [
        "| Phase | Required skills | Optional skills | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for index, unit in enumerate(units, start=1):
        routed_unit = with_plan_first_for_unit(unit)
        lines.append(
            " | ".join(
                [
                    f"| Commit {index}: {routed_unit['title']}",
                    render_skills_cell(routed_unit.get("required_skills", [])),
                    render_skills_cell(routed_unit.get("optional_skills", [])),
                    routed_unit.get("skill_routing_evidence") or "-",
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


def with_plan_first_for_unit(unit: dict) -> dict:
    routed = dict(unit)
    required_skills = list(routed.get("required_skills") or [])
    if unit_requires_plan_first(routed) and PLAN_FIRST_SKILL not in required_skills:
        insert_plan_first_skill(required_skills)
    routed["required_skills"] = required_skills
    return routed


def insert_plan_first_skill(required_skills: list[str]) -> None:
    if "요청개선" in required_skills:
        required_skills.insert(required_skills.index("요청개선") + 1, PLAN_FIRST_SKILL)
        return
    required_skills.insert(0, PLAN_FIRST_SKILL)


def unit_requires_plan_first(unit: dict) -> bool:
    required_skills = unit.get("required_skills") or []
    if PLAN_FIRST_SKILL in required_skills:
        return True
    title = str(unit.get("title") or "")
    evidence = str(unit.get("skill_routing_evidence") or "")
    allowed_paths = " ".join(str(item) for item in unit.get("allowed_paths") or [])
    combined = f"{title} {evidence} {allowed_paths}"
    if PLAN_FIRST_EXEMPT_RE.search(combined) and not PLAN_FIRST_TRIGGER_RE.search(combined):
        return False
    return bool(PLAN_FIRST_TRIGGER_RE.search(combined))


def manifest_row_requires_plan_first(phase: str, evidence: str) -> bool:
    if phase.strip().lower() == "final gate":
        return False
    combined = f"{phase} {evidence}"
    if PLAN_FIRST_EXEMPT_RE.search(combined) and not PLAN_FIRST_TRIGGER_RE.search(combined):
        return False
    return bool(PLAN_FIRST_TRIGGER_RE.search(combined))


def repair_plan_first_manifest_policy(plan_path: Path) -> bool:
    content = plan_path.read_text(encoding="utf-8")
    section_match = re.search(
        r"(^##\s+Skill Routing Manifest\s*$)(.*?)(?=^##\s+|\Z)",
        content,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not section_match:
        return False
    section = section_match.group(0)
    repaired_lines = [repair_plan_first_manifest_row(line) for line in section.splitlines()]
    repaired_section = "\n".join(repaired_lines)
    if section.endswith("\n"):
        repaired_section += "\n"
    if repaired_section == section:
        return False
    repaired = content[: section_match.start()] + repaired_section + content[section_match.end() :]
    plan_path.write_text(repaired, encoding="utf-8")
    return True


def repair_plan_first_manifest_row(line: str) -> str:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return line
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    if len(cells) < 4:
        return line
    phase, required_cell, optional_cell, evidence = cells[:4]
    if phase.lower() == "phase" or all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells if cell.strip()):
        return line
    required_skills = parse_skills_cell(required_cell)
    if PLAN_FIRST_SKILL in required_skills or not manifest_row_requires_plan_first(phase, evidence):
        return line
    insert_plan_first_skill(required_skills)
    return f"| {phase} | {render_skills_cell(required_skills)} | {optional_cell or '-'} | {evidence or '-'} |"


def parse_skills_cell(cell: str) -> list[str]:
    quoted = re.findall(r"`([^`]+)`", cell)
    if quoted:
        return quoted
    stripped = cell.strip()
    if not stripped or stripped == "-":
        return []
    return [item.strip() for item in stripped.split(",") if item.strip()]


def ensure_plan_skill_routing_manifest(plan_path: Path, queue_data: dict) -> bool:
    content = plan_path.read_text(encoding="utf-8")
    if re.search(r"^##\s+Skill Routing Manifest\s*$", content, flags=re.MULTILINE):
        return repair_plan_first_manifest_policy(plan_path)
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
    repair_plan_first_manifest_policy(plan_path)
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
    plan_file = plan_dir / "plan.md"
    if not queue_json.exists():
        if plan_file.exists():
            return plan_dir, plan_readiness.sync_queue_cache_from_plan(plan_file)
        raise SystemExit(f"Missing queue: {queue_json}")
    queue_data = json.loads(queue_json.read_text(encoding="utf-8"))
    if plan_file.exists():
        ensure_plan_skill_routing_manifest(plan_file, queue_data)
        queue_data = plan_readiness.sync_queue_cache_from_plan(plan_file)
    return plan_dir, queue_data


def load_queue_read_only(plan_path: str | Path) -> tuple[Path, dict]:
    """Read an existing queue without initialization, cache repair, or writes."""
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


def execution_context_metadata(context: ExecutionWorktreeContext) -> dict:
    metadata = {
        "source_repo": str(context.source_repo),
        "execution_repo": str(context.execution_repo),
        "worktree_path": str(context.worktree_path),
        "branch": context.branch,
        "plan_id": context.plan_id,
        "source_plan_sha256": context.source_plan_sha256,
        "cleanup_state": context.cleanup_state,
    }
    return {**metadata, "execution_context": dict(metadata)}


def persist_execution_context(plan_dir: str | Path, context: ExecutionWorktreeContext) -> dict:
    directory = Path(plan_dir).expanduser().resolve()
    queue_path = directory / "queue.json"
    queue_data = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.exists() else {}
    queue_data.update(execution_context_metadata(context))
    save_queue(directory, queue_data)
    return queue_data


def execution_context_for_plan(plan_path: str | Path, queue_data: dict | None = None) -> ExecutionWorktreeContext:
    path = Path(plan_path).expanduser().resolve()
    plan_dir = path.parent if path.name == "plan.md" else path
    data = queue_data
    if data is None:
        queue_path = plan_dir / "queue.json"
        data = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.exists() else {}
    fallback_repo = plan_dir.parents[2]
    source_repo = Path(data.get("source_repo") or fallback_repo).expanduser().resolve()
    execution_repo = Path(data.get("execution_repo") or fallback_repo).expanduser().resolve()
    return ExecutionWorktreeContext(
        source_repo=source_repo,
        execution_repo=execution_repo,
        worktree_path=Path(data.get("worktree_path") or execution_repo).expanduser().resolve(),
        plan_id=str(data.get("plan_id") or data.get("plan_slug") or plan_dir.name),
        branch=str(data.get("branch") or f"codex/{plan_dir.name}"),
        source_plan_sha256=str(data.get("source_plan_sha256") or (data.get("source_plan") or {}).get("sha256") or ""),
        cleanup_state=str(data.get("cleanup_state") or "not_applicable"),
    )


def execution_repo_for_plan(plan_path: str | Path, queue_data: dict | None = None) -> Path:
    return execution_context_for_plan(plan_path, queue_data).execution_repo


def cleanup_plan_worktree(plan_path: str | Path, target_branch: str) -> ExecutionWorktreeContext:
    plan_dir, queue_data = load_queue(plan_path)
    context = execution_context_for_plan(plan_dir, queue_data)
    cleaned = cleanup_execution_worktree(context, target_branch=target_branch)
    persist_execution_context(plan_dir, cleaned)
    return cleaned


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
    flow = state.paths(repo)
    active: list[ActivePlan] = []
    for plan_path in sorted(flow.plans.glob("*/plan.md")):
        queue_json = plan_path.parent / "queue.json"
        try:
            queue_data = _read_queue_projection(plan_path, queue_json)
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
    flow = state.paths(repo)
    plan_paths = sorted(flow.plans.glob("*/plan.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    for plan_path in plan_paths:
        queue_json = plan_path.parent / "queue.json"
        try:
            queue_data = _read_queue_projection(plan_path, queue_json)
        except (OSError, ValueError):
            continue
        return ActivePlan(queue_json.parent, queue_json.parent / "plan.md", queue_json, queue_data)
    return None


def _read_queue_projection(plan_path: Path, queue_json: Path) -> dict:
    if queue_json.exists():
        return json.loads(queue_json.read_text(encoding="utf-8"))
    content = plan_path.read_text(encoding="utf-8")
    log_path = plan_path.parent / "log.md"
    log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    readiness = plan_readiness.check_plan_ready(content, log)
    return {
        "plan_title": plan_readiness.title_from_plan(content, plan_path.parent.name),
        "branch": plan_readiness.branch_name_from_plan(content, f"codex/{plan_path.parent.name}"),
        "units": [
            {
                "id": unit.unit_id,
                "number": unit.number,
                "title": unit.title,
                "status": "done" if unit.number in readiness.completed else "ready",
            }
            for unit in plan_readiness.parse_commit_units(content)
        ],
    }


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
