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


@dataclass(frozen=True)
class ExtractedPaths:
    allowed_paths: list[str]
    external_allowed_paths: list[str]


COMMIT_RE = re.compile(r"^###\s+Commit\s+(\d+):\s*(.+?)\s*$", re.MULTILINE)
PHASE_RE = re.compile(r"^###\s+Phase\s+(\d+):?\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")


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


def extract_allowed_paths(excerpt: str, repo: str | Path) -> list[str]:
    return extract_path_scope(excerpt, repo).allowed_paths


def extract_path_scope(excerpt: str, repo: str | Path) -> ExtractedPaths:
    repo_path = Path(repo).expanduser().resolve()
    allowed_paths: list[str] = []
    external_allowed_paths: list[str] = []
    seen_allowed: set[str] = set()
    seen_external: set[str] = set()
    for match in CODE_SPAN_RE.finditer(excerpt):
        candidate = normalize_allowed_path_candidate(match.group(1), repo_path)
        if not candidate:
            continue
        if Path(candidate).is_absolute():
            if candidate not in seen_external:
                seen_external.add(candidate)
                external_allowed_paths.append(candidate)
            continue
        if candidate not in seen_allowed:
            seen_allowed.add(candidate)
            allowed_paths.append(candidate)
    return ExtractedPaths(allowed_paths=allowed_paths, external_allowed_paths=external_allowed_paths)


def normalize_allowed_path_candidate(value: str, repo: Path) -> str:
    candidate = value.strip().strip(".,;:")
    if not candidate:
        return ""
    if re.match(r"^[a-z][a-z0-9+.-]*://", candidate, flags=re.IGNORECASE):
        return ""
    if any(char.isspace() for char in candidate):
        return ""
    if "<" in candidate or ">" in candidate:
        return ""
    if "/" not in candidate and "\\" not in candidate and "*" not in candidate:
        return ""
    candidate = candidate.replace("\\", "/")
    if candidate.startswith("/"):
        repo_text = str(repo)
        if candidate == repo_text:
            return "."
        if candidate.startswith(repo_text + "/"):
            return candidate[len(repo_text) + 1 :]
        return candidate
    while candidate.startswith("./"):
        candidate = candidate[2:]
    return candidate
