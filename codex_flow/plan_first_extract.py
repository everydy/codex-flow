from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from . import source_plan


@dataclass(frozen=True)
class ExtractedTicket:
    id: str
    title: str
    source_section: str
    excerpt: str
    confidence: str


COMMIT_RE = re.compile(r"^###\s+Commit\s+(\d+):\s*(.+?)\s*$", re.MULTILINE)
PHASE_RE = re.compile(r"^###\s+Phase\s+(\d+):?\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)


def extract_tickets(source_content: str) -> list[ExtractedTicket]:
    matches = list(COMMIT_RE.finditer(source_content))
    confidence = "high"
    if not matches:
        matches = list(PHASE_RE.finditer(source_content))
        confidence = "medium"
    if not matches:
        return [
            ExtractedTicket(
                id="ticket-001",
                title="Execute source plan",
                source_section="#",
                excerpt=source_content[:2400],
                confidence="low",
            )
        ]
    tickets: list[ExtractedTicket] = []
    for index, match in enumerate(matches, start=1):
        start = match.start()
        end = matches[index].start() if index < len(matches) else len(source_content)
        tickets.append(
            ExtractedTicket(
                id=f"ticket-{index:03d}",
                title=match.group(2).strip(),
                source_section=match.group(0).strip(),
                excerpt=source_content[start:end].strip(),
                confidence=confidence,
            )
        )
    return tickets


def overall_confidence(tickets: list[ExtractedTicket]) -> str:
    if not tickets:
        return "low"
    values = {ticket.confidence for ticket in tickets}
    if "low" in values:
        return "low"
    if "medium" in values:
        return "medium"
    return "high"


def excerpt_hash(value: str) -> str:
    return source_plan.sha256_text(value)[:16]


def write_ticket_files(directory: Path, tickets: list[ExtractedTicket]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for ticket in tickets:
        path = directory / f"{ticket.id}.md"
        path.write_text(render_ticket_md(ticket), encoding="utf-8")


def render_ticket_md(ticket: ExtractedTicket) -> str:
    return "\n".join(
        [
            f"# {ticket.id}: {ticket.title}",
            "",
            f"- Source section: {ticket.source_section}",
            f"- Extraction confidence: {ticket.confidence}",
            "",
            "## Source Excerpt",
            "",
            "```md",
            ticket.excerpt.strip(),
            "```",
            "",
        ]
    )
