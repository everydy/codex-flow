from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .codex_cli import fence, last_matching_line, parse_key_values, run_codex_exec


@dataclass(frozen=True)
class MergeAgentInput:
    repo: Path
    plan_path: Path
    source_branch: str
    target_branch: str
    merge_mode: str
    git_status: str
    failed_merge_command: str


@dataclass(frozen=True)
class MergeAgentResult:
    status: str
    summary: str = ""
    reason: str = ""


class MergeAgent(Protocol):
    def resolve_conflicts(self, input_data: MergeAgentInput) -> MergeAgentResult:
        ...


class CodexMergeAgent:
    def __init__(self, command: str = "codex", extra_args: list[str] | None = None) -> None:
        self.command = command
        self.extra_args = extra_args or []

    def resolve_conflicts(self, input_data: MergeAgentInput) -> MergeAgentResult:
        result = run_codex_exec(
            build_merge_agent_prompt(input_data),
            repo=input_data.repo,
            command=self.command,
            sandbox="workspace-write",
            extra_args=self.extra_args,
        )
        return parse_merge_agent_result(result.final_message)


def build_merge_agent_prompt(input_data: MergeAgentInput) -> str:
    try:
        plan_path = str(input_data.plan_path.relative_to(input_data.repo))
    except ValueError:
        plan_path = str(input_data.plan_path)
    return "\n".join(
        [
            "You are the merge conflict resolver for the Codex Flow workflow orchestrator.",
            "Resolve only the current git merge conflicts and help verify the result.",
            "Do not implement new features, advance plan commit units, rewrite the plan, create commits, switch branches, abort merges, push, or open PRs.",
            "Only edit files that are necessary to resolve the active merge conflict.",
            "",
            "When finished, return exactly one final line in one of these forms:",
            'MERGE_READY summary="..."',
            'MERGE_NEEDS_WORK reason="..."',
            "",
            "Merge context:",
            f"Repo root: {input_data.repo}",
            f"Plan path: {plan_path}",
            f"Source branch: {input_data.source_branch}",
            f"Target branch: {input_data.target_branch}",
            f"Merge mode: {input_data.merge_mode}",
            "",
            "Current git status:",
            fence(input_data.git_status) if input_data.git_status.strip() else "Clean",
            "",
            "Failed merge command summary:",
            fence(input_data.failed_merge_command) if input_data.failed_merge_command.strip() else "Not provided",
        ]
    )


def parse_merge_agent_result(text: str) -> MergeAgentResult:
    line = last_matching_line(text, "MERGE_")
    values = parse_key_values(line)
    if line.startswith("MERGE_READY"):
        return MergeAgentResult("ready", summary=values.get("summary", "Merge conflicts resolved."))
    if line.startswith("MERGE_NEEDS_WORK"):
        return MergeAgentResult("needs_work", reason=values.get("reason", "Merge agent requested more work."))
    raise ValueError(f"Unknown merge agent result: {line}")
