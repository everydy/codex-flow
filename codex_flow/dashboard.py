from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path

from . import inbox, plan_readiness, plans, pr, source_plan, state
from .attempt_supervisor import DiagnosticRedactionError, redact_diagnostic
from .git_ops import dirty_paths, status
from .tickets import list_tickets


def render_dashboard(repo: str | Path | None = None) -> str:
    flow = state.ensure_initialized(repo)
    summary = state.dashboard_summary(flow.repo)
    pr_lock = pr.read_pr_lock(flow.repo)
    dirty = dirty_paths(status(flow.repo)) if (flow.repo / ".git").exists() else []
    inbox_count = len(inbox.read_inbox_requests(flow.inbox)) + len([ticket for ticket in list_tickets(flow.repo) if ticket.status == "inbox"])
    active_plans = plans.list_active_plans(flow.repo)

    lines = [
        "# Codex Flow Dashboard",
        "",
        f"State: {flow.root}",
        f"PR lock: {'active - ' + str(pr_lock) if pr_lock else 'none'}",
        f"Inbox requests: {inbox_count}",
        f"Dirty files: {len(dirty)}",
        f"Plans: {summary['plans']}",
        f"Ready units: {summary['ready_units']}",
        f"Done units: {summary['done_units']}",
        f"Needs work units: {summary['needs_work_units']}",
        "",
    ]
    if dirty:
        lines.extend(["## Dirty Files", "", *[f"- {path}" for path in dirty], ""])
    if not active_plans:
        lines.extend(["## Active Plans", "", "- None", ""])
    else:
        lines.extend(["## Active Plans", ""])
        for active in active_plans:
            plan_content = active.plan_path.read_text(encoding="utf-8") if active.plan_path.exists() else ""
            log_path = active.directory / "log.md"
            log_content = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            readiness = plan_readiness.check_plan_ready(plan_content, log_content)
            units = plan_readiness.parse_commit_units(plan_content)
            done = len(readiness.completed)
            ready = len([unit for unit in active.queue_data.get("units", []) if unit.get("status") == "ready"])
            needs_work = len([unit for unit in active.queue_data.get("units", []) if unit.get("status") == "needs_work"])
            branch = plan_readiness.branch_name_from_plan(plan_content, active.queue_data.get("branch", "-"))
            title = plan_readiness.title_from_plan(plan_content, active.queue_data.get("plan_title") or active.queue_data.get("ticket_title") or active.directory.name)
            rel_plan = state.relative_to_repo(flow, active.plan_path)
            drift = source_plan.check_source_drift(active.directory)
            lines.extend(
                [
                    f"### {title}",
                    "",
                    f"- Plan: `{rel_plan}`",
                    f"- Branch: `{branch}`",
                    f"- Progress: {done}/{len(units)} done, {ready} ready, {needs_work} needs_work",
                    f"- Next: {format_next(readiness.next_unit)}",
                ]
            )
            current = current_queue_unit(active.queue_data)
            if current is not None:
                lines.extend(format_current_unit(active.directory, current))
            if drift.reason != "no source metadata":
                lines.append(f"- Source drift: {drift.reason}")
            lines.extend(
                [
                    f"- Suggested command: `python3 scripts/codex_flow.py run-all --plan {rel_plan} --auto-resolve`",
                    "",
                ]
            )
            recent_log = recent_log_lines(active.directory / "log.md")
            if recent_log:
                lines.extend(["Recent log:", *[f"- {line}" for line in recent_log], ""])
    return "\n".join(lines).rstrip()


def recent_log_lines(log_path: Path, limit: int = 3) -> list[str]:
    if not log_path.exists():
        return []
    lines = [line.strip("- ").strip() for line in log_path.read_text(encoding="utf-8").splitlines() if line.startswith("- ")]
    return [safe_operator_text(line, limit=240) for line in lines[-limit:]]


def format_next(unit: plan_readiness.CommitUnit | None) -> str:
    if unit is None:
        return "complete"
    return f"Commit {unit.number} - {unit.title}"


def current_queue_unit(queue_data: dict) -> dict | None:
    return next(
        (unit for unit in queue_data.get("units", []) if isinstance(unit, dict) and unit.get("status") != "done"),
        None,
    )


def format_current_unit(plan_dir: Path, unit: dict) -> list[str]:
    ledger, source = read_current_ledger(plan_dir, unit)
    policy = unit.get("execution_policy") if isinstance(unit.get("execution_policy"), dict) else {}
    adapter = str(policy.get("executor_adapter") or "").replace("_", "-")
    owner = str(unit.get("execution_owner") or ("isolated-child" if adapter == "isolated-child" else "main"))
    unit_status = str(unit.get("status") or "unknown")
    ledger_status = str(ledger.get("status") or "not recorded")
    phase = str(ledger.get("phase") or ("main" if owner == "main" and unit_status == "in_progress" else unit_status))
    elapsed = format_elapsed(ledger, str(unit.get("updated_at") or ""))
    last_progress = format_last_progress(ledger, str(unit.get("updated_at") or ""))
    waiting = format_wait_reason(unit, ledger)
    verification = "; ".join(
        safe_operator_text(str(item), limit=120) for item in unit.get("verification", []) if str(item).strip()
    ) or "not recorded"
    revision = ledger.get("_revision")
    source_label = source + (f" r{revision}" if revision is not None else "")
    return [
        f"- Current owner: `{owner}`",
        f"- Current phase/status: `{phase}` / `{ledger_status if source.startswith('attempt ledger') else unit_status}`",
        f"- Elapsed: {elapsed}",
        f"- Last progress: {last_progress}",
        f"- Waiting: {waiting}",
        f"- Current verification: {verification}",
        f"- Status source: {source_label}",
    ]


def read_current_ledger(plan_dir: Path, unit: dict) -> tuple[dict, str]:
    ledger_path = current_ledger_path(plan_dir, unit)
    if ledger_path is None or not ledger_path.exists():
        return {}, "queue fallback (ledger not recorded)"
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        record = dict(payload.get("record") or {})
        record["_revision"] = int(payload.get("revision") or 0)
        return record, "attempt ledger"
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}, "queue fallback (ledger unreadable)"


def current_ledger_path(plan_dir: Path, unit: dict) -> Path | None:
    main_ledger = unit.get("main_unit_ledger")
    if isinstance(main_ledger, str) and main_ledger.strip():
        candidate = (plan_dir / main_ledger).resolve()
        try:
            candidate.relative_to(plan_dir.resolve())
        except ValueError:
            return None
        return candidate
    unit_id = str(unit.get("id") or "").strip()
    if not unit_id:
        return None
    try:
        attempt = max(0, int(unit.get("repair_attempts") or 0))
    except (TypeError, ValueError):
        return None
    return plan_dir / "attempts" / unit_id / f"attempt-{attempt}" / "attempt-ledger.json"


def format_elapsed(ledger: dict, updated_at: str) -> str:
    raw_ms = ledger.get("elapsed_ms", ledger.get("heartbeat_elapsed_ms"))
    if isinstance(raw_ms, (int, float)):
        return f"{format_duration(max(0, int(raw_ms)) // 1000)} (ledger)"
    try:
        elapsed = max(0, int((datetime.now() - datetime.fromisoformat(updated_at)).total_seconds()))
    except (TypeError, ValueError):
        return "not recorded"
    return f"{format_duration(elapsed)} (since queue update)"


def format_last_progress(ledger: dict, updated_at: str) -> str:
    event = str(ledger.get("last_event") or "").strip()
    age_ms = ledger.get("child_output_age_ms")
    if event and isinstance(age_ms, (int, float)):
        return f"{safe_operator_text(event)}; child output age {format_duration(max(0, int(age_ms)) // 1000)}"
    if event:
        return safe_operator_text(event)
    return f"queue updated {updated_at}" if updated_at else "not recorded"


def format_wait_reason(unit: dict, ledger: dict) -> str:
    unit_status = str(unit.get("status") or "unknown")
    failure_class = str(unit.get("failure_class") or "").strip()
    raw_reason = str(unit.get("last_needs_work_reason") or ledger.get("reason") or "").strip()
    if unit_status == "needs_work":
        prefix = failure_class or "needs_work"
        return f"{prefix}: {safe_operator_text(raw_reason)}" if raw_reason else prefix
    if unit_status == "human_gate":
        return "operator decision required"
    ledger_status = str(ledger.get("status") or "")
    if unit_status == "in_progress" or ledger_status in {"open", "running", "starting"}:
        return "no wait recorded (running)"
    return "not waiting"


def safe_operator_text(value: str, limit: int = 180) -> str:
    try:
        redacted = redact_diagnostic(value, os.environ)
    except DiagnosticRedactionError:
        return "[redaction failed]"
    one_line = " ".join(redacted.split())
    if len(one_line) <= limit:
        return one_line or "not recorded"
    return one_line[: max(0, limit - 1)].rstrip() + "…"


def format_duration(seconds: int) -> str:
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"
