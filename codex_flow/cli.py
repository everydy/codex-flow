from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from . import briefs, dashboard as dashboard_view, inbox, plan_readiness, plans, pr, runner, source_plan, state, tickets
from .run_all import RunAllRunner
from .attempt_ledger import AttemptLedger, LedgerConflict
from .cancellation import CancellationError, request_cancel
from .git_ops import head_summary, scoped_diff_digest
from .main_unit import MainUnitError, begin_main_unit, complete_main_unit, hold_main_unit
from .final_gate import FinalGateError, produce_final_gate
from .merge import MergeRunner


FAILURE_ACTIONS = {
    "branch_finalization_blocked",
    "hard_stop",
    "human_gate",
    "max_units_reached",
    "merge_needs_work",
    "needs_work",
    "not_ready",
    "pr_locked",
    "source_drift",
}


def exit_code_for_action(action: str) -> int:
    return 1 if action in FAILURE_ACTIONS else 0


def default_commit(enabled: bool | None, *, executes_work: bool) -> bool:
    if enabled is not None:
        return enabled
    return executes_work


def default_execute(enabled: bool, *, preview: bool, dry_run: bool) -> bool:
    if preview or dry_run:
        return False
    return True


def default_execute_units(enabled: bool | None, *, auto_resolve: bool) -> bool:
    if enabled is not None:
        return enabled
    return auto_resolve


def default_repair_attempts(value: int | None, *, auto_resolve: bool) -> int:
    if value is not None:
        if value < 0:
            raise SystemExit("--repair-attempts must be 0 or greater")
        return value
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-flow",
        description="Local 구현커밋 / implementation-commit ticket, plan, and review queue for Codex work.",
    )
    parser.add_argument("--repo", type=Path, default=None, help="Repository root to operate on. Defaults to cwd or nearest git root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create .codex-flow state directories.")

    def add_route_source_args(command: argparse.ArgumentParser) -> None:
        command.add_argument("source_plan", type=Path)
        command.add_argument("--auto-resolve", action="store_true", help="Let 구현커밋 resolve route blockers without waiting for the user.")
        command.add_argument("--branch", help="Branch name to use when creating a new plan.")
        command.add_argument("--title", dest="plan_title", help="Plan title to use when creating a new plan.")
        command.add_argument(
            "--isolated-worktree",
            action="store_true",
            help="Run the plan in an additional external Git worktree instead of the default repository working tree.",
        )
        command.add_argument(
            "--worktree-root",
            type=Path,
            help="Root directory for generated task worktrees. Requires --isolated-worktree.",
        )

    route = subparsers.add_parser("route", help="Adopt a plan-first Markdown source and create an execution queue.")
    add_route_source_args(route)

    subparsers.add_parser("status", help="Print numeric dashboard summary.")
    dashboard = subparsers.add_parser("dashboard", help="Print detailed dashboard.")
    dashboard.add_argument("--watch", action="store_true")
    dashboard.add_argument("--interval", type=float, default=5.0)

    plan = subparsers.add_parser("plan", help="Create a plan directory and queue from a ticket.")
    plan.add_argument("--ticket", type=Path, help="Ticket Markdown path. Defaults to first inbox ticket.")

    run_next = subparsers.add_parser("run-next", help="Execute the next implementer unit by default; use --preview or --dry-run to inspect prompts only.")
    run_next.add_argument("--plan", type=Path, required=True)
    run_next.add_argument("--dry-run", action="store_true", help="Render the next prompt without writing it or changing queue state.")
    run_next.add_argument("--preview", action="store_true", help="Write the next prompt and mark the unit prompted instead of executing it.")
    run_next.add_argument("--execute", action="store_true", help="Compatibility flag; execution is already the default unless --preview or --dry-run is used.")
    run_next.add_argument("--commit", dest="commit", action="store_true", default=None, help="Commit changed files after successful execution. This is the default for run-next.")
    run_next.add_argument("--no-commit", dest="commit", action="store_false", help="Leave successful execution changes uncommitted.")
    run_next.add_argument("--codex-command", default="codex")
    run_next.add_argument("--codex-arg", action="append", default=[])
    run_next.add_argument("--codex-timeout-seconds", type=int, default=900)
    run_next.add_argument("--allow-dirty", action="store_true")
    run_next.add_argument("--no-branch", action="store_true")
    run_next.add_argument("--auto-resolve", action="store_true", help="Auto-preserve dirty worktree state and continue when safe.")
    run_next.add_argument("--repair-attempts", type=int, default=None, help="Retry a needs_work unit this many times. Defaults to 1 with --auto-resolve, otherwise 0.")
    run_next.add_argument("--accept-source-drift", action="store_true", help="Continue even if the original source plan changed after route.")

    run_all = subparsers.add_parser("run-all", help="Execute remaining commit units and locally merge completed plans by default; use --preview, --dry-run, or --no-merge to avoid merge.")
    run_all.add_argument("--plan", type=Path, required=True)
    run_all.add_argument("--max-units", type=int, default=None)
    run_all.add_argument("--dry-run", action="store_true", help="Render the next prompt without writing it or changing queue state.")
    run_all.add_argument("--preview", action="store_true", help="Write prompts and mark units prompted instead of executing them.")
    run_all.add_argument("--execute", action="store_true", help="Compatibility flag; execution is already the default unless --preview or --dry-run is used.")
    run_all.add_argument("--commit", dest="commit", action="store_true", default=None, help="Commit changed files after each successful executed unit. This is the default for run-all.")
    run_all.add_argument("--no-commit", dest="commit", action="store_false", help="Leave successful execution changes uncommitted.")
    run_all.add_argument("--codex-command", default="codex")
    run_all.add_argument("--codex-arg", action="append", default=[])
    run_all.add_argument("--codex-timeout-seconds", type=int, default=900)
    run_all.add_argument("--allow-dirty", action="store_true")
    run_all.add_argument("--no-branch", action="store_true")
    run_all.add_argument("--auto-resolve", action="store_true", help="Auto-preserve dirty worktree state and continue when safe.")
    run_all.add_argument("--repair-attempts", type=int, default=None, help="Retry a needs_work unit this many times. Defaults to 1 with --auto-resolve, otherwise 0.")
    run_all.add_argument("--accept-source-drift", action="store_true", help="Continue even if the original source plan changed after route.")
    run_all.add_argument("--merge", action="store_true", help="Compatibility flag; completed run-all merges locally by default.")
    run_all.add_argument("--no-merge", action="store_true", help="Leave the completed work branch open instead of locally merging and closing it.")
    run_all.add_argument("--target", default="main")
    run_all.add_argument("--remote", action="store_true", help="Use remote PR/merge mode for finalize steps.")
    run_all.add_argument("--open-pr", action="store_true", help="Create a PR dry-run or remote PR after all units are done.")

    cleanup_worktree = subparsers.add_parser("cleanup-worktree", help="Remove a clean task worktree after its branch is merged.")
    cleanup_worktree.add_argument("--plan", type=Path, required=True)
    cleanup_worktree.add_argument("--target", default="main")

    mark = subparsers.add_parser("mark", help="Update a queue unit status.")
    mark.add_argument("--plan", type=Path, required=True)
    mark.add_argument("--unit", required=True)
    mark.add_argument("--status", required=True, choices=sorted(state.UNIT_STATUSES))

    adopt = subparsers.add_parser("adopt-attempt", help="Validate and adopt a held attempt without rollback.")
    adopt.add_argument("--attempt", type=Path, required=True, help="Attempt directory or attempt-ledger.json path.")
    adopt.add_argument("--expected-revision", type=int, required=True)
    adopt.add_argument("--evidence", type=Path, required=True)

    cancel = subparsers.add_parser(
        "request-cancel", help="Request candidate-preserving cancellation of one isolated attempt."
    )
    cancel.add_argument("--plan", type=Path, required=True)
    cancel.add_argument("--unit", required=True)
    cancel.add_argument("--attempt", type=int, required=True)

    begin_main = subparsers.add_parser("begin-main-unit", help="Lock the next unit for direct main-agent implementation.")
    begin_main.add_argument("--plan", type=Path, required=True)
    begin_main.add_argument("--unit")
    begin_main.add_argument(
        "--retry-held",
        action="store_true",
        help="Explicitly open a new main attempt for an unchanged held candidate.",
    )

    complete_main = subparsers.add_parser("complete-main-unit", help="Verify and exact-commit an open main-agent unit.")
    complete_main.add_argument("--plan", type=Path, required=True)
    complete_main.add_argument("--unit", required=True)
    complete_main.add_argument("--expected-revision", type=int, required=True)
    complete_main.add_argument("--evidence", type=Path, required=True)
    complete_main.add_argument("--message", required=True)

    hold_main = subparsers.add_parser("hold-main-unit", help="Hold a candidate without rollback or automatic retry.")
    hold_main.add_argument("--plan", type=Path, required=True)
    hold_main.add_argument("--unit", required=True)
    hold_main.add_argument("--expected-revision", type=int, required=True)
    hold_main.add_argument("--failure-class", required=True)
    hold_main.add_argument("--reason", required=True)

    final_gate = subparsers.add_parser("write-final-gate", help="Bind cumulative review and canonical tests to the terminal exact HEAD.")
    final_gate.add_argument("--plan", type=Path, required=True)
    final_gate.add_argument("--review-evidence", type=Path, required=True)
    final_gate.add_argument("--test-evidence", type=Path, required=True)

    subparsers.add_parser("morning-brief", help="Write today's morning review brief.")

    review = subparsers.add_parser("review", help="Write a review checklist for a plan.")
    review.add_argument("--plan", type=Path, required=True)

    def add_pr_finalize_args(command: argparse.ArgumentParser, include_remote: bool = True) -> None:
        command.add_argument("--plan", type=Path, required=True)
        command.add_argument("--ready", action="store_true", help="Create a ready PR instead of draft when using remote mode.")
        command.add_argument("--auto-resolve", action="store_true", help="Run unfinished units before creating the PR artifact.")
        command.add_argument("--execute-units", dest="execute_units", action="store_true", default=None, help="Compatibility flag; auto-resolve executes units by default.")
        command.add_argument("--no-execute-units", dest="execute_units", action="store_false", help="Auto-resolve by generating prompts only instead of running Codex CLI.")
        command.add_argument("--commit", dest="commit", action="store_true", default=None, help="Commit auto-resolved unit changes. This is the default when auto-resolve executes units.")
        command.add_argument("--no-commit", dest="commit", action="store_false", help="Leave auto-resolved unit changes uncommitted.")
        command.add_argument("--max-units", type=int, default=8)
        command.add_argument("--codex-command", default="codex")
        command.add_argument("--codex-arg", action="append", default=[])
        command.add_argument("--codex-timeout-seconds", type=int, default=900)
        command.add_argument("--allow-dirty", action="store_true")
        command.add_argument("--no-branch", action="store_true")
        command.add_argument("--repair-attempts", type=int, default=None, help="Retry a needs_work unit this many times while auto-resolving. Defaults to 1 with --auto-resolve, otherwise 0.")
        command.add_argument("--accept-source-drift", action="store_true", help="Continue even if the original source plan changed after route.")
        if include_remote:
            command.add_argument("--remote", action="store_true", help="Create a real remote draft PR with gh.")

    open_pr = subparsers.add_parser("open-pr", help="Write a PR dry-run artifact or create a remote draft PR.")
    open_pr.add_argument("--dry-run", action="store_true", default=True)
    add_pr_finalize_args(open_pr)

    create_pr = subparsers.add_parser("create-pr", help="Create a real remote draft PR after all units are ready.")
    add_pr_finalize_args(create_pr, include_remote=False)

    pr_check = subparsers.add_parser("pr-check", help="Check PR lock state.")
    pr_check.add_argument("--gh-command", default="gh")
    subparsers.add_parser("drain", help="Route one inbox ticket when no PR lock is active.")

    set_lock = subparsers.add_parser("set-pr-lock", help="Create or replace the PR lock.")
    set_lock.add_argument("--branch", required=True)
    set_lock.add_argument("--pr-url", required=True)
    set_lock.add_argument("--status", default="reviewing")

    subparsers.add_parser("clear-pr-lock", help="Remove the active PR lock.")

    merge = subparsers.add_parser("merge", help="Merge a completed plan; --auto-resolve can finish units first.")
    merge.add_argument("--plan", type=Path)
    merge.add_argument("--target", default="main")
    merge.add_argument("--execute", action="store_true")
    merge.add_argument("--remote", action="store_true")
    merge.add_argument("--auto-resolve", action="store_true", help="Auto-complete unfinished units and treat the merge request as executable.")
    merge.add_argument("--execute-units", dest="execute_units", action="store_true", default=None, help="Compatibility flag; auto-resolve executes units by default.")
    merge.add_argument("--no-execute-units", dest="execute_units", action="store_false", help="Auto-resolve by generating prompts only instead of running Codex CLI.")
    merge.add_argument("--commit", dest="commit", action="store_true", default=None, help="Commit auto-resolved unit changes. This is the default when auto-resolve executes units.")
    merge.add_argument("--no-commit", dest="commit", action="store_false", help="Leave auto-resolved unit changes uncommitted.")
    merge.add_argument("--max-units", type=int, default=8)
    merge.add_argument("--codex-command", default="codex")
    merge.add_argument("--codex-arg", action="append", default=[])
    merge.add_argument("--codex-timeout-seconds", type=int, default=900)
    merge.add_argument("--allow-dirty", action="store_true")
    merge.add_argument("--no-branch", action="store_true")
    merge.add_argument("--repair-attempts", type=int, default=None, help="Retry a needs_work unit this many times while auto-resolving. Defaults to 1 with --auto-resolve, otherwise 0.")
    merge.add_argument("--accept-source-drift", action="store_true", help="Continue even if the original source plan changed after route.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        flow = state.ensure_initialized(args.repo)
        print(f"initialized: {flow.root}")
        return 0

    if args.command == "route":
        if args.worktree_root is not None and not args.isolated_worktree:
            print("--worktree-root requires --isolated-worktree")
            return 1
        try:
            source = source_plan.resolve_source_plan(args.source_plan, repo=args.repo)
        except SystemExit as exc:
            print(str(exc))
            return 1
        lock = pr.read_pr_lock(args.repo)
        if lock:
            repo = state.resolve_repo(args.repo)
            inbox.append_inbox_request(repo, str(source.path), "PR lock is active; source plan route deferred.")
            print("route: queued source plan due to active PR lock")
            return 0
        plan = plans.create_plan_from_source(
            source,
            repo=args.repo,
            branch_name=args.branch,
            plan_title=args.plan_title,
            prepare_git_branch=args.isolated_worktree,
            worktree_root=args.worktree_root,
        )
        print(f"source_plan_adopted: {plan.directory / 'source-plan.md'}")
        print(f"plan_created: {plan.plan_path}")
        print(f"queue_created: {plan.queue_md}")
        return 0

    if args.command == "cleanup-worktree":
        try:
            context = plans.cleanup_plan_worktree(args.plan, args.target)
        except SystemExit as exc:
            print(str(exc))
            return 1
        if context.mode == "in_place":
            print(f"worktree_cleanup_not_applicable: {context.source_repo}")
        else:
            print(f"worktree_cleaned: {context.worktree_path}")
        return 0

    if args.command == "status":
        summary = state.dashboard_summary(args.repo)
        for key, value in summary.items():
            print(f"{key}: {value}")
        return 0

    if args.command == "dashboard":
        if args.watch:
            try:
                while True:
                    print(dashboard_view.render_dashboard(args.repo))
                    time.sleep(args.interval)
            except KeyboardInterrupt:
                return 0
        print(dashboard_view.render_dashboard(args.repo))
        return 0

    if args.command == "plan":
        ticket = args.ticket
        if ticket is None:
            first_ticket = tickets.first_inbox_ticket(args.repo)
            if first_ticket is None:
                raise SystemExit("No inbox ticket found.")
            ticket = first_ticket.path
        plan = plans.create_plan_from_ticket(ticket, repo=args.repo)
        print(f"plan_created: {plan.plan_path}")
        print(f"queue_created: {plan.queue_md}")
        return 0

    if args.command == "run-next":
        execute_work = default_execute(args.execute, preview=args.preview, dry_run=args.dry_run)
        repair_attempts = default_repair_attempts(args.repair_attempts, auto_resolve=args.auto_resolve)
        result = runner.run_next(
            args.plan,
            dry_run=args.dry_run,
            execute=execute_work,
            commit=default_commit(args.commit, executes_work=execute_work),
            codex_command=args.codex_command,
            codex_args=args.codex_arg,
            allow_dirty=args.allow_dirty,
            no_branch=args.no_branch,
            auto_resolve=args.auto_resolve,
            repair_attempts=repair_attempts,
            accept_source_drift=args.accept_source_drift,
            codex_timeout_seconds=args.codex_timeout_seconds,
        )
        if result is None:
            print("no_ready_units")
            return 0
        print(f"unit: {result['unit']['id']}")
        print(f"prompt: {result['prompt_path']}")
        if result.get("action") == "source_drift":
            print(f"action: {result.get('action')}")
            if result.get("reason"):
                print(f"reason: {result['reason']}")
            if result.get("source_path"):
                print(f"source_path: {result['source_path']}")
            return exit_code_for_action(result.get("action", ""))
        if result.get("action") == "human_gate":
            print(f"action: {result.get('action')}")
            if result.get("reason"):
                print(f"reason: {result['reason']}")
            return exit_code_for_action(result.get("action", ""))
        if args.dry_run:
            print("dry_run: prompt not written and queue not changed")
        elif execute_work:
            print(f"action: {result.get('action')}")
            if result.get("commit"):
                print(f"commit: {result['commit']}")
            if result.get("changed_paths"):
                print(f"changed_paths: {', '.join(result['changed_paths'])}")
            if result.get("auto_resolved_dirty"):
                print(f"auto_resolved_dirty: {', '.join(result['auto_resolved_dirty'])}")
            if result.get("repair_attempts"):
                print(f"repair_attempts: {result['repair_attempts']}")
            if result.get("diagnostic_path"):
                print(f"diagnostic_path: {result['diagnostic_path']}")
            return exit_code_for_action(result.get("action", ""))
        else:
            print("status: prompted")
        return 0

    if args.command == "begin-main-unit":
        try:
            contract = begin_main_unit(args.plan, unit_id=args.unit, retry_held=args.retry_held)
        except MainUnitError as exc:
            print(f"main_unit_rejected: {exc}")
            return 1
        print(json.dumps({
            "unit_id": contract.unit_id,
            "owner": "main",
            "ledger_path": str(contract.ledger_path),
            "ledger_revision": contract.ledger_revision,
            "expected_head": contract.expected_head,
            "allowed_paths": list(contract.allowed_paths),
            "queue_revision": contract.queue_revision,
            "status": contract.status,
            "attempt": contract.attempt,
        }, ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "request-cancel":
        try:
            marker = request_cancel(args.plan, unit_id=args.unit, attempt=args.attempt)
        except CancellationError as exc:
            print(f"cancel_rejected: {exc}")
            return 1
        print(f"cancel_requested: {marker}")
        return 0

    if args.command == "complete-main-unit":
        try:
            evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
            result = complete_main_unit(
                args.plan,
                unit_id=args.unit,
                expected_revision=args.expected_revision,
                evidence=evidence,
                message=args.message,
            )
        except (MainUnitError, OSError, json.JSONDecodeError) as exc:
            print(f"main_unit_rejected: {exc}")
            return 1
        print(f"main_unit_completed: unit={result.unit_id} revision={result.ledger_revision} commit={result.commit}")
        return 0

    if args.command == "hold-main-unit":
        try:
            result = hold_main_unit(
                args.plan,
                unit_id=args.unit,
                expected_revision=args.expected_revision,
                failure_class=args.failure_class,
                reason=args.reason,
            )
        except MainUnitError as exc:
            print(f"main_unit_rejected: {exc}")
            return 1
        print(f"main_unit_held: unit={result.unit_id} revision={result.ledger_revision}")
        return 0

    if args.command == "write-final-gate":
        try:
            review_evidence = json.loads(args.review_evidence.read_text(encoding="utf-8"))
            test_evidence = json.loads(args.test_evidence.read_text(encoding="utf-8"))
            record = produce_final_gate(
                args.plan,
                review_evidence=review_evidence,
                test_evidence=test_evidence,
            )
        except (FinalGateError, OSError, json.JSONDecodeError) as exc:
            print(f"final_gate_rejected: {exc}")
            return 1
        print(f"final_gate_written: head={record.reviewed_head} evidence={record.evidence_hash}")
        return 0

    if args.command == "run-all":
        execute_work = default_execute(args.execute, preview=args.preview, dry_run=args.dry_run)
        repair_attempts = default_repair_attempts(args.repair_attempts, auto_resolve=args.auto_resolve)
        run_result = RunAllRunner().run_all(
            args.plan,
            max_units=args.max_units,
            dry_run=args.dry_run,
            execute=execute_work,
            commit=default_commit(args.commit, executes_work=execute_work),
            codex_command=args.codex_command,
            codex_args=args.codex_arg,
            allow_dirty=args.allow_dirty,
            no_branch=args.no_branch,
            auto_resolve=args.auto_resolve,
            repair_attempts=repair_attempts,
            accept_source_drift=args.accept_source_drift,
            codex_timeout_seconds=args.codex_timeout_seconds,
            open_pr=args.open_pr,
            merge=args.merge,
            remote=args.remote,
            target=args.target,
            no_merge=args.no_merge,
        )
        results = run_result.steps
        print(f"units_processed: {len(results)}")
        for result in results:
            suffix = f" {result.get('action')}" if result.get("action") else ""
            commit_suffix = f" commit={result['commit']}" if result.get("commit") else ""
            repair_suffix = f" repair_attempts={result['repair_attempts']}" if result.get("repair_attempts") else ""
            reason_suffix = f" reason={result['reason']}" if result.get("reason") else ""
            diagnostic_suffix = f" diagnostic_path={result['diagnostic_path']}" if result.get("diagnostic_path") else ""
            print(f"- {result['unit']['id']}: {result['prompt_path']}{suffix}{commit_suffix}{repair_suffix}{reason_suffix}{diagnostic_suffix}")
        if run_result.message:
            print(run_result.message)
        return exit_code_for_action(run_result.action)

    if args.command == "mark":
        unit = plans.mark_unit(args.plan, args.unit, args.status)
        print(f"unit_marked: {unit['id']} -> {unit['status']}")
        return 0
    if args.command == "adopt-attempt":
        ledger_path = args.attempt if args.attempt.name.endswith(".json") else args.attempt / "attempt-ledger.json"
        try:
            evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
            ledger = AttemptLedger(ledger_path)
            current = ledger.load()
            repo = state.resolve_repo(args.repo)
            allowed_paths = [str(path) for path in current.record.get("allowed_paths", [])]
            adopted = AttemptLedger(ledger_path).adopt_attempt(
                expected_revision=args.expected_revision,
                evidence=evidence,
                current_head=head_summary(repo) or "",
                current_scoped_diff_digest=scoped_diff_digest(repo, allowed_paths),
                current_full_diff_digest=scoped_diff_digest(repo, []),
            )
        except (LedgerConflict, ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"attempt_adoption_rejected: {exc}")
            return 1
        print(f"attempt_adopted: revision={adopted.revision}")
        return 0

    if args.command == "morning-brief":
        brief = briefs.write_morning_brief(args.repo)
        print(f"morning_brief: {brief}")
        return 0

    if args.command == "review":
        review_path = briefs.write_review(args.plan)
        print(f"review: {review_path}")
        return 0

    if args.command in {"open-pr", "create-pr"}:
        if args.auto_resolve:
            auto_complete_result = auto_complete_units(args)
            if auto_complete_result:
                print(auto_complete_result)
                if "source_drift" in auto_complete_result:
                    return 1
        remote_pr_requested = args.command == "create-pr" or getattr(args, "remote", False)
        if remote_pr_requested:
            try:
                url, lock_path = pr.create_remote_pr(args.plan, draft=not args.ready)
            except pr.ActivePrLockError as exc:
                print(str(exc))
                return 1
            except SystemExit as exc:
                print(str(exc))
                return 1
            print(f"remote_pr: {url}")
            print(f"pr_lock: {lock_path}")
            return 0
        pr_path = pr.write_pr_dry_run(args.plan)
        print(f"pr_dry_run: {pr_path}")
        print("remote_pr: dry_run")
        return 0

    if args.command == "pr-check":
        print(pr.check_pr_lock(args.repo, gh_command=args.gh_command))
        return 0

    if args.command == "drain":
        print(pr.drain_inbox(args.repo))
        return 0

    if args.command == "set-pr-lock":
        lock_path = pr.write_pr_lock(state.resolve_repo(args.repo), args.branch, args.pr_url, args.status)
        print(f"set_pr_lock: {lock_path}")
        return 0

    if args.command == "clear-pr-lock":
        print("clear_pr_lock: removed" if pr.clear_pr_lock(args.repo) else "clear_pr_lock: no lock")
        return 0

    if args.command == "merge":
        if not args.plan:
            raise SystemExit("--plan is required")
        if args.auto_resolve:
            auto_complete_result = auto_complete_units(args)
            if auto_complete_result:
                print(auto_complete_result)
                if "source_drift" in auto_complete_result:
                    return 1
        merge_runner = MergeRunner()
        result = (
            merge_runner.merge_remote(args.plan, target=args.target, execute=args.execute or args.auto_resolve)
            if args.remote
            else merge_runner.merge_local(args.plan, target=args.target, execute=args.execute or args.auto_resolve)
        )
        print(result.message)
        if result.action in {"hard_stop", "merge_needs_work", "needs_work"}:
            return 2
        return exit_code_for_action(result.action)

    parser.print_help()
    return 1


def auto_complete_units(args: argparse.Namespace) -> str:
    plan_dir, queue_data = plans.load_queue(args.plan)
    queue_data = plan_readiness.sync_queue_cache_from_plan(plan_dir / "plan.md")
    unfinished_before = plans.unfinished_units(queue_data)
    if not unfinished_before:
        return ""
    requeued = plans.requeue_unfinished_units(args.plan, reason=f"auto-resolve before {args.command}")
    execute_units = default_execute_units(args.execute_units, auto_resolve=args.auto_resolve)
    repair_attempts = default_repair_attempts(args.repair_attempts, auto_resolve=args.auto_resolve)
    results = runner.run_all(
        args.plan,
        max_units=args.max_units,
        execute=execute_units,
        commit=default_commit(args.commit, executes_work=execute_units),
        codex_command=args.codex_command,
        codex_args=args.codex_arg,
        allow_dirty=args.allow_dirty,
        no_branch=args.no_branch,
        auto_resolve=args.auto_resolve,
        repair_attempts=repair_attempts,
        accept_source_drift=getattr(args, "accept_source_drift", False),
        codex_timeout_seconds=args.codex_timeout_seconds,
    )
    refreshed_dir, refreshed = plans.load_queue(args.plan)
    refreshed = plan_readiness.sync_queue_cache_from_plan(refreshed_dir / "plan.md")
    remaining = plans.unfinished_units(refreshed)
    source_drift_count = sum(1 for result in results if result.get("action") == "source_drift")
    drift_suffix = f" source_drift={source_drift_count}" if source_drift_count else ""
    return (
        "auto_resolve_units: "
        f"unfinished_before={len(unfinished_before)} "
        f"requeued={len(requeued)} "
        f"processed={len(results)} "
        f"remaining={len(remaining)}"
        f"{drift_suffix}"
    )


def finalize_after_run_all(args: argparse.Namespace) -> str:
    if not (args.merge or args.open_pr or args.remote):
        return ""
    plan_dir, plan_content, log_content = plan_readiness.read_plan_file(args.plan)
    readiness = plan_readiness.check_plan_ready(plan_content, log_content)
    if not readiness.ready:
        return f"finalize: not_ready {readiness.reason}"
    if args.merge:
        return pr.merge_plan(args.plan, target=args.target, remote=args.remote, execute=True)
    if args.remote:
        try:
            url, lock_path = pr.create_remote_pr(args.plan, draft=True)
        except pr.ActivePrLockError as exc:
            return str(exc)
        except SystemExit as exc:
            return str(exc)
        return f"opened_pr: {url}\npr_lock: {lock_path}"
    pr_path = pr.write_pr_dry_run(args.plan)
    return f"pr_dry_run: {pr_path}"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
