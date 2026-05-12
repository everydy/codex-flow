from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from . import state


@dataclass(frozen=True)
class Ticket:
    id: str
    title: str
    path: Path
    status: str
    priority: str
    project: str
    created_at: str
    allow_draft_pr: bool
    no_implement: bool


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"true", "yes", "1", "y"}


def parse_frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    data: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip().strip('"')
    return data


def load_ticket(path: str | Path) -> Ticket:
    ticket_path = Path(path).expanduser().resolve()
    data = parse_frontmatter(ticket_path)
    return Ticket(
        id=data.get("id", ticket_path.stem),
        title=data.get("title", ticket_path.stem),
        path=ticket_path,
        status=data.get("status", "inbox"),
        priority=data.get("priority", "normal"),
        project=data.get("project", "default"),
        created_at=data.get("created_at", ""),
        allow_draft_pr=_parse_bool(data.get("allow_draft_pr", "false")),
        no_implement=_parse_bool(data.get("no_implement", "false")),
    )


def update_ticket_status(path: str | Path, status: str) -> None:
    ticket_path = Path(path).expanduser().resolve()
    text = ticket_path.read_text(encoding="utf-8")
    if re.search(r"^status:\s*.+$", text, flags=re.MULTILINE):
        text = re.sub(r"^status:\s*.+$", f"status: {status}", text, count=1, flags=re.MULTILINE)
    else:
        text = text.replace("---\n", f"---\nstatus: {status}\n", 1)
    ticket_path.write_text(text, encoding="utf-8")


def submit_ticket(
    title: str,
    repo: str | Path | None = None,
    priority: str = "normal",
    project: str = "default",
    allow_draft_pr: bool = False,
    no_implement: bool = False,
) -> Ticket:
    flow = state.ensure_initialized(repo)
    date_prefix = state.today()
    sequence = state.next_sequence(flow.tickets, date_prefix)
    ticket_id = f"{date_prefix}-{sequence:03d}"
    slug = state.slugify(title, fallback="ticket")
    ticket_path = flow.tickets / f"{ticket_id}-{slug}.md"
    created = state.timestamp()

    content = [
        "---",
        f'id: "{ticket_id}"',
        f'title: "{title}"',
        "status: inbox",
        f"priority: {priority}",
        f"project: {project}",
        f"created_at: {created}",
        f"allow_draft_pr: {_bool_text(allow_draft_pr)}",
        f"no_implement: {_bool_text(no_implement)}",
        "---",
        "",
        f"# {title}",
        "",
        "## Request",
        "",
        title,
        "",
        "## Notes",
        "",
        "- Created by Codex Flow.",
        "",
    ]
    ticket_path.write_text("\n".join(content), encoding="utf-8")
    append_inbox(flow, ticket_id, title, created)
    state.refresh_dashboard(flow.repo)
    return load_ticket(ticket_path)


def append_inbox(flow: state.FlowPaths, ticket_id: str, title: str, created: str) -> None:
    line = f"| [{ticket_id}](tickets/{ticket_id}-{state.slugify(title, fallback='ticket')}.md) | inbox | {created} |"
    current = flow.inbox.read_text(encoding="utf-8") if flow.inbox.exists() else "# Codex Flow Inbox\n"
    if ticket_id not in current:
        flow.inbox.write_text(current.rstrip() + "\n" + line + "\n", encoding="utf-8")


def list_tickets(repo: str | Path | None = None) -> list[Ticket]:
    flow = state.ensure_initialized(repo)
    return [load_ticket(path) for path in sorted(flow.tickets.glob("*.md"))]


def first_inbox_ticket(repo: str | Path | None = None) -> Ticket | None:
    for ticket in list_tickets(repo):
        if ticket.status == "inbox":
            return ticket
    return None
