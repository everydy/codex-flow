from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import re

from .codex_cli import fence, last_matching_line, parse_key_values, run_codex_exec
from . import plan_readiness, state


@dataclass(frozen=True)
class ActivePlanInfo:
    path: Path
    branch: str
    title: str
    plan_content: str
    queue_content: str


@dataclass(frozen=True)
class RouterAgentInput:
    repo: Path
    prompt: str
    pr_lock: str | None
    active_plans: list[ActivePlanInfo]


@dataclass(frozen=True)
class RouterAgentDecision:
    action: str
    reason: str
    plan_path: str = ""
    branch_name: str = ""
    plan_title: str = ""


class RouterAgent(Protocol):
    def decide(self, input_data: RouterAgentInput) -> RouterAgentDecision:
        ...


class CodexRouterAgent:
    def __init__(self, command: str = "codex", extra_args: list[str] | None = None) -> None:
        self.command = command
        self.extra_args = extra_args or []

    def decide(self, input_data: RouterAgentInput) -> RouterAgentDecision:
        result = run_codex_exec(
            build_router_prompt(input_data),
            repo=input_data.repo,
            command=self.command,
            sandbox="read-only",
            extra_args=self.extra_args,
        )
        return parse_route_decision(result.final_message)


class HeuristicRouterAgent:
    def decide(self, input_data: RouterAgentInput) -> RouterAgentDecision:
        if input_data.pr_lock:
            return RouterAgentDecision("pause_for_pr_review", "PR lock is active.")
        if len(input_data.active_plans) == 1:
            return RouterAgentDecision(
                "existing_plan",
                "Only one active plan exists.",
                plan_path=str(input_data.active_plans[0].path),
            )
        request_terms = set(state.slugify(input_data.prompt, fallback="request").split("-"))
        best: tuple[int, ActivePlanInfo] | None = None
        for plan in input_data.active_plans:
            score = len(request_terms & set(state.slugify(plan.title, fallback="plan").split("-")))
            if score and (best is None or score > best[0]):
                best = (score, plan)
        if best:
            return RouterAgentDecision("existing_plan", "Request terms match an active plan.", plan_path=str(best[1].path))
        title = first_line(input_data.prompt)[:80] or "User Request"
        return RouterAgentDecision("new_plan", "No matching active plan.", branch_name=f"codex/{state.slugify(title)}", plan_title=title)


def build_router_prompt(input_data: RouterAgentInput) -> str:
    active = "\n\n".join(format_active_plan(input_data.repo, plan) for plan in input_data.active_plans) or "None"
    return "\n".join(
        [
            "You are Agent 0: Router for the Codex Flow workflow orchestrator.",
            "Decide where the new user request should go. Do not edit files.",
            "",
            "Routing rules:",
            "- If a PR lock is active, pause new planning.",
            "- If the request strongly depends on an active plan, route it to that plan.",
            "- Otherwise create a new plan.",
            "",
            "Return exactly one line in one of these forms:",
            'ROUTE existing_plan planPath=".codex-flow/plans/<name>/plan.md" reason="..."',
            'ROUTE new_plan branchName="codex/<name>" planTitle="..." reason="..."',
            'ROUTE pause_for_pr_review reason="..."',
            "",
            "User request:",
            fence(input_data.prompt),
            "",
            "PR lock:",
            fence(input_data.pr_lock) if input_data.pr_lock else "None",
            "",
            "Active plans:",
            active,
        ]
    )


def parse_route_decision(text: str) -> RouterAgentDecision:
    line = last_matching_line(text, "ROUTE ")
    match = re.match(r"^ROUTE\s+(\S+)(?:\s+(.*))?$", line)
    if not match:
        raise ValueError(f"Invalid router decision: {line}")
    action = match.group(1)
    values = parse_key_values(match.group(2) or "")
    reason = values.get("reason", "Router selected this route.")
    if action == "existing_plan":
        plan_path = values.get("planPath", "")
        if not plan_path:
            raise ValueError("Router decision missing planPath")
        return RouterAgentDecision(action, reason, plan_path=plan_path)
    if action == "new_plan":
        return RouterAgentDecision(action, reason, branch_name=values.get("branchName", ""), plan_title=values.get("planTitle", ""))
    if action == "pause_for_pr_review":
        return RouterAgentDecision(action, reason)
    raise ValueError(f"Unknown router action: {action}")


def collect_active_plans(repo: str | Path) -> list[ActivePlanInfo]:
    flow = state.ensure_initialized(repo)
    active: list[ActivePlanInfo] = []
    for plan_path in sorted(flow.plans.glob("*/plan.md")):
        plan_content = plan_path.read_text(encoding="utf-8")
        log_content = (plan_path.parent / "log.md").read_text(encoding="utf-8") if (plan_path.parent / "log.md").exists() else ""
        readiness = plan_readiness.check_plan_ready(plan_content, log_content)
        if readiness.ready:
            continue
        queue_path = plan_path.parent / "requests.md"
        if not queue_path.exists():
            queue_path = plan_path.parent / "queue.md"
        active.append(
            ActivePlanInfo(
                path=plan_path,
                branch=plan_readiness.branch_name_from_plan(plan_content, f"codex/{plan_path.parent.name}"),
                title=plan_readiness.title_from_plan(plan_content, plan_path.parent.name),
                plan_content=plan_content,
                queue_content=queue_path.read_text(encoding="utf-8") if queue_path.exists() else "",
            )
        )
    return active


def format_active_plan(repo: Path, plan: ActivePlanInfo) -> str:
    try:
        rel = plan.path.relative_to(repo)
    except ValueError:
        rel = plan.path
    return "\n".join(
        [
            f"Path: {rel}",
            f"Branch: {plan.branch}",
            f"Title: {plan.title}",
            "",
            "plan.md:",
            fence(plan.plan_content),
            "",
            "queue:",
            fence(plan.queue_content) if plan.queue_content.strip() else "None",
        ]
    )


def first_line(value: str) -> str:
    for line in value.splitlines():
        if line.strip():
            return line.strip()
    return ""
