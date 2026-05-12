from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import re

from .codex_cli import fence, last_matching_line, run_codex_exec


@dataclass(frozen=True)
class PlannerAgentInput:
    repo: Path
    branch_name: str
    plan_title: str
    plan_path: Path
    queue_path: Path
    log_path: Path
    prompt: str
    reason: str


@dataclass(frozen=True)
class PlannerAgentResult:
    path: str
    final_message: str


class PlannerAgent(Protocol):
    def write_plan(self, input_data: PlannerAgentInput) -> PlannerAgentResult:
        ...


class TemplatePlannerAgent:
    def write_plan(self, input_data: PlannerAgentInput) -> PlannerAgentResult:
        return PlannerAgentResult(path=str(input_data.plan_path), final_message=f'PLAN_WRITTEN path="{input_data.plan_path}"')


class CodexPlannerAgent:
    def __init__(self, command: str = "codex", extra_args: list[str] | None = None) -> None:
        self.command = command
        self.extra_args = extra_args or []

    def write_plan(self, input_data: PlannerAgentInput) -> PlannerAgentResult:
        plan_content = input_data.plan_path.read_text(encoding="utf-8") if input_data.plan_path.exists() else ""
        result = run_codex_exec(
            build_planner_prompt(input_data, plan_content),
            repo=input_data.repo,
            command=self.command,
            sandbox="workspace-write",
            extra_args=self.extra_args,
        )
        return PlannerAgentResult(path=parse_plan_written(result.final_message), final_message=result.final_message)


def build_planner_prompt(input_data: PlannerAgentInput, plan_content: str = "") -> str:
    plan_path = relative_path(input_data.repo, input_data.plan_path)
    queue_path = relative_path(input_data.repo, input_data.queue_path)
    log_path = relative_path(input_data.repo, input_data.log_path)
    return "\n".join(
        [
            "You are Agent 1: Planner for the Codex Flow workflow orchestrator.",
            "Rewrite the plan file for the new branch. Do not edit source code or any file other than the plan file.",
            "",
            "Planning rules:",
            "- Keep the plan as readable Markdown, not JSON.",
            "- Split the work into clear commit-sized units using headings like `### Commit 1: ...`.",
            "- Keep each unit concrete enough for a fresh Codex session to implement later.",
            "- Avoid overengineering; prefer clean, simple, readable steps.",
            "- Preserve the Branch and Title lines.",
            "",
            "When the plan is written, return exactly one final line:",
            f'PLAN_WRITTEN path="{plan_path}"',
            "",
            "Plan metadata:",
            f"Branch: {input_data.branch_name}",
            f"Title: {input_data.plan_title}",
            f"Plan path: {plan_path}",
            f"Queue path: {queue_path}",
            f"Log path: {log_path}",
            "",
            "Router reason:",
            fence(input_data.reason),
            "",
            "User request:",
            fence(input_data.prompt),
            "",
            "Current plan.md content:",
            fence(plan_content) if plan_content.strip() else "None",
        ]
    )


def parse_plan_written(text: str) -> str:
    line = last_matching_line(text, "PLAN_WRITTEN ")
    match = re.match(r'^PLAN_WRITTEN\s+path=(?:"([^"]*)"|\'([^\']*)\'|(\S+))$', line)
    if not match:
        raise ValueError(f"Invalid planner response: {line}")
    return match.group(1) or match.group(2) or match.group(3)


def relative_path(repo: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)
