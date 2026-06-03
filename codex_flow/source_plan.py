from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json

from . import state


@dataclass(frozen=True)
class SourcePlan:
    path: Path
    repo: Path
    content: str
    sha256: str
    title: str


@dataclass(frozen=True)
class SourceDrift:
    changed: bool
    reason: str
    source_path: Path | None = None


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def title_from_markdown(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or fallback
    return fallback


def relative_path(path: Path, repo: Path) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def resolve_source_plan(value: str | Path, repo: str | Path | None = None) -> SourcePlan:
    repo_path = state.resolve_repo(repo)
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = repo_path / candidate
    candidate = candidate.resolve()
    if not candidate.is_file() or candidate.suffix.lower() not in {".md", ".markdown"}:
        raise SystemExit(
            "route requires a plan-first Markdown file path. "
            "Create a plan-first document first, then pass its Markdown path to route."
        )
    content = candidate.read_text(encoding="utf-8")
    title = title_from_markdown(content, candidate.stem)
    return SourcePlan(path=candidate, repo=repo_path, content=content, sha256=sha256_text(content), title=title)


def snapshot_source_plan(source: SourcePlan, plan_dir: Path, extraction_confidence: str) -> None:
    (plan_dir / "source-plan.md").write_text(source.content, encoding="utf-8")
    metadata = {
        "source_path": relative_path(source.path, source.repo),
        "source_sha256": source.sha256,
        "source_title": source.title,
        "adopted_at": state.timestamp(),
        "route_mode": "plan_first_source",
        "extraction_confidence": extraction_confidence,
    }
    (plan_dir / "source.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check_source_drift(plan_dir: str | Path) -> SourceDrift:
    directory = Path(plan_dir).expanduser().resolve()
    metadata_path = directory / "source.json"
    if not metadata_path.exists():
        return SourceDrift(False, "no source metadata")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    source_path = Path(metadata["source_path"])
    if not source_path.is_absolute():
        source_path = directory.parents[2] / source_path
    if not source_path.exists():
        return SourceDrift(True, "source file missing", source_path)
    current = sha256_text(source_path.read_text(encoding="utf-8"))
    if current != metadata["source_sha256"]:
        return SourceDrift(True, "source changed", source_path)
    return SourceDrift(False, "clean", source_path)
