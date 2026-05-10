from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tempfile

from .git_ops import run_process


@dataclass(frozen=True)
class AgentDecision:
    status: str
    title: str
    summary: str
    reason: str
    raw: str


@dataclass(frozen=True)
class AgentResult:
    status: int
    stdout: str
    stderr: str
    final_message: str
    decision: AgentDecision


def build_codex_exec_args(output_path: Path, repo: str | Path, extra_args: list[str] | None = None) -> list[str]:
    return [
        "exec",
        "--json",
        "--cd",
        str(Path(repo).resolve()),
        "--sandbox",
        "workspace-write",
        "--output-last-message",
        str(output_path),
        *(extra_args or []),
        "-",
    ]


def run_codex_agent(
    prompt: str,
    repo: str | Path,
    command: str = "codex",
    extra_args: list[str] | None = None,
) -> AgentResult:
    with tempfile.TemporaryDirectory(prefix="codex-flow-agent-") as temp_dir:
        output_path = Path(temp_dir) / "last-message.txt"
        result = run_process([command, *build_codex_exec_args(output_path, repo, extra_args)], cwd=repo, input_text=prompt)
        final_message = output_path.read_text(encoding="utf-8") if output_path.exists() else result.stdout
    if result.status != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip() or f"{command} failed")
    decision = parse_agent_decision(final_message)
    return AgentResult(
        status=result.status,
        stdout=result.stdout,
        stderr=result.stderr,
        final_message=final_message,
        decision=decision,
    )


def parse_agent_decision(text: str) -> AgentDecision:
    decision_line = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("COMMIT_UNIT_READY") or stripped.startswith("COMMIT_UNIT_NEEDS_WORK"):
            decision_line = stripped
    if not decision_line:
        return AgentDecision(
            status="ready",
            title="Commit unit ready",
            summary=first_non_empty_line(text) or "Codex finished without an explicit decision line.",
            reason="",
            raw=text,
        )

    values = parse_key_values(decision_line)
    if decision_line.startswith("COMMIT_UNIT_NEEDS_WORK"):
        return AgentDecision(
            status="needs_work",
            title="",
            summary="",
            reason=values.get("reason", "Codex reported needs_work."),
            raw=text,
        )
    return AgentDecision(
        status="ready",
        title=values.get("title", "Commit unit ready"),
        summary=values.get("summary", "Ready to commit."),
        reason="",
        raw=text,
    )


def parse_key_values(line: str) -> dict[str, str]:
    return {
        match.group(1): match.group(2) or match.group(3) or match.group(4) or ""
        for match in re.finditer(r'(\w+)=(?:"([^"]*)"|\'([^\']*)\'|(\S+))', line)
    }


def first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""

