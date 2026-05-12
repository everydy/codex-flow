from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import re

from . import state


@dataclass(frozen=True)
class QueuedRequest:
    prompt: str
    reason: str
    received_at: str


@dataclass(frozen=True)
class DrainResult:
    action: str
    drained: int = 0
    remaining: int = 0
    message: str = ""


RouteRequest = Callable[[QueuedRequest], str]
LockReader = Callable[[], bool]


def append_inbox_request(repo: str | Path, prompt: str, reason: str, received_at: str | None = None) -> Path:
    flow = state.ensure_initialized(repo)
    requests = read_inbox_requests(flow.inbox)
    requests.append(QueuedRequest(prompt=prompt, reason=reason, received_at=received_at or state.timestamp()))
    write_inbox_requests(flow.inbox, requests)
    return flow.inbox


def read_inbox_requests(inbox_path: str | Path) -> list[QueuedRequest]:
    path = Path(inbox_path)
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8")
    requests = parse_structured_requests(content)
    return requests


def write_inbox_requests(inbox_path: str | Path, requests: list[QueuedRequest]) -> Path:
    path = Path(inbox_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Codex Flow Inbox", ""]
    for request in requests:
        lines.extend(
            [
                "## Request",
                "",
                f"- Received: {request.received_at}",
                f"- Reason: {request.reason}",
                "",
                "```text",
                request.prompt.strip(),
                "```",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def drain_inbox_requests(inbox_path: str | Path, has_lock: LockReader, route_request: RouteRequest) -> DrainResult:
    requests = read_inbox_requests(inbox_path)
    if not requests:
        return DrainResult("empty", message="drain: empty")
    if has_lock():
        return DrainResult("locked", remaining=len(requests), message=f"drain: locked with {len(requests)} request(s) remaining")
    drained = 0
    for index, request in enumerate(requests):
        if has_lock():
            remaining = len(requests) - index
            write_inbox_requests(inbox_path, requests[index:])
            return DrainResult("locked", drained=drained, remaining=remaining, message=f"drain: locked with {remaining} request(s) remaining")
        route_request(request)
        drained += 1
        write_inbox_requests(inbox_path, requests[index + 1 :])
    return DrainResult("drained", drained=drained, message=f"drain: routed {drained} request(s)")


def parse_structured_requests(content: str) -> list[QueuedRequest]:
    sections = re.split(r"^## Request\s*$", content, flags=re.MULTILINE)[1:]
    requests: list[QueuedRequest] = []
    for section in sections:
        received = match_line(section, r"^- Received:\s*(.+?)\s*$") or state.timestamp()
        reason = match_line(section, r"^- Reason:\s*(.+?)\s*$") or "Queued."
        prompt_match = re.search(r"```(?:text)?\s*(.*?)```", section, flags=re.DOTALL)
        prompt = prompt_match.group(1).strip() if prompt_match else ""
        if prompt:
            requests.append(QueuedRequest(prompt=prompt, reason=reason, received_at=received))
    return requests


def match_line(content: str, pattern: str) -> str:
    match = re.search(pattern, content, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""
