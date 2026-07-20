from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .codex_cli import fence, parse_session_id, run_codex_exec
from . import execution_policy, plan_readiness
from .plan_readiness import CommitUnit


@dataclass(frozen=True)
class ImplementerAgentInput:
    repo: Path
    plan_path: Path
    plan_content: str
    unit: CommitUnit
    previous_commit: str | None
    git_status: str
    repair_attempt: int = 0
    repair_reason: str = ""
    execution_policy: dict | None = None
    allowed_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImplementerAgentResult:
    session_id: str
    implementation_message: str


class ImplementerAgent(Protocol):
    def implement(
        self,
        input_data: ImplementerAgentInput,
        diagnostic_dir: Path | None = None,
        timeout_seconds: int | None = None,
        attempt_ledger_path: Path | None = None,
        diff_probe=None,
        liveness_probe=None,
    ) -> ImplementerAgentResult:
        ...


class CodexImplementerAgent:
    def __init__(
        self,
        command: str = "codex",
        extra_args: list[str] | None = None,
        child_home: str | Path | None = None,
    ) -> None:
        self.command = command
        self.extra_args = extra_args or []
        self.child_home = child_home

    def implement(
        self,
        input_data: ImplementerAgentInput,
        diagnostic_dir: Path | None = None,
        timeout_seconds: int | None = None,
        attempt_ledger_path: Path | None = None,
        diff_probe=None,
        liveness_probe=None,
    ) -> ImplementerAgentResult:
        policy = execution_policy.classify_execution_policy(
            {
                "title": input_data.unit.title,
                "content": input_data.unit.content,
                "allowed_paths": input_data.allowed_paths,
                "execution_policy": input_data.execution_policy or {},
            }
        )
        implementation = run_codex_exec(
            build_implementation_prompt(input_data),
            repo=input_data.repo,
            command=self.command,
            sandbox="workspace-write",
            extra_args=self.extra_args,
            timeout_seconds=timeout_seconds,
            diagnostic_dir=diagnostic_dir / "implementation" if diagnostic_dir else None,
            phase="implementation",
            child_home=self.child_home,
            execution_profile=policy.effective_profile,
            attempt_ledger_path=attempt_ledger_path,
            diff_probe=diff_probe,
            liveness_probe=liveness_probe,
        )
        session_id = parse_session_id(implementation.stdout) or ""
        if not session_id:
            raise SystemExit("Codex implementer did not report a session id")
        return ImplementerAgentResult(
            session_id=session_id,
            implementation_message=implementation.final_message,
        )


def build_implementation_prompt(input_data: ImplementerAgentInput) -> str:
    try:
        plan_path = str(input_data.plan_path.relative_to(input_data.repo))
    except ValueError:
        plan_path = str(input_data.plan_path)
    skill_routing = plan_readiness.format_skill_routing_entry(
        plan_readiness.skill_routing_for_commit(input_data.plan_content, input_data.unit.number)
    )
    return "\n".join(
        [
            f"Read {plan_path} and implement only commit unit {input_data.unit.number}.",
            "",
            "You are Agent 2: Implementer for the Codex Flow workflow orchestrator.",
            "Implement only the selected commit unit. Do not create a git commit.",
            "Before editing, load and apply every required skill named in the Skill Routing Manifest.",
            "Required skills are mandatory and were attested before this session; if any cannot be loaded, stop without editing and return needs_work.",
            "Optional skills may be skipped only with an explicit reason in the review summary.",
            "",
            "Selected commit unit:",
            fence(f"### Commit {input_data.unit.number}: {input_data.unit.title}\n\n{input_data.unit.content}"),
            "",
            "Skill Routing Manifest entry:",
            fence(skill_routing),
            "",
            "Previous commit:",
            input_data.previous_commit or "None",
            "",
            "Git status before this unit:",
            fence(input_data.git_status) if input_data.git_status.strip() else "Clean",
            "",
            *repair_context(input_data),
            "Unit boundary gates:",
            "- Do not create or merge a real remote PR inside this commit-unit implementation; finalization commands handle PR and merge after all units are ready.",
            "- Do not deploy.",
            "- Do not reset, checkout, or revert unrelated user changes.",
            "- If secrets, accounts, payments, or external posting are required, stop after preparing drafts.",
            "",
            "Full plan.md:",
            fence(input_data.plan_content),
        ]
    )


def repair_context(input_data: ImplementerAgentInput) -> list[str]:
    if input_data.repair_attempt <= 0:
        return []
    return [
        "Repair attempt:",
        fence(
            "\n".join(
                [
                    f"Attempt: {input_data.repair_attempt}",
                    f"Previous needs_work reason: {input_data.repair_reason or 'Not provided'}",
                    "Keep any useful existing working tree changes from the previous attempt.",
                    "Do not restart the unit from scratch unless the current partial changes are directly wrong.",
                    "Focus only on resolving the reason above and returning the commit unit to a ready state.",
                ]
            )
        ),
        "",
    ]
