from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

from . import briefs, dashboard as dashboard_view, inbox, plan_readiness, plans, pr, runner, state, tickets
from .planner_agent import CodexPlannerAgent, TemplatePlannerAgent
from .router_agent import CodexRouterAgent, HeuristicRouterAgent, RouterAgentDecision, RouterAgentInput, collect_active_plans
from .run_all import RunAllRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-flow", description="Local ticket, plan, and review queue for Codex work.")
    parser.add_argument("--repo", type=Path, default=None, help="Repository root to operate on. Defaults to cwd or nearest git root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create .codex-flow state directories.")

    def add_ticket_args(command: argparse.ArgumentParser) -> None:
        command.add_argument("title")
        command.add_argument("--priority", default="normal")
        command.add_argument("--project", default="default")
        command.add_argument("--allow-draft-pr", action="store_true")
        command.add_argument("--no-implement", action="store_true")
        command.add_argument("--auto-resolve", action="store_true", help="Let Codex Flow resolve route blockers without waiting for the user.")
        command.add_argument("--plan", type=Path, help="Append the request to an existing plan instead of creating a new one.")
        command.add_argument("--branch", help="Branch name to use when creating a new plan.")
        command.add_argument("--title", dest="plan_title", help="Plan title to use when creating a new plan.")
        command.add_argument("--reason", default="Routed by Codex Flow.")
        command.add_argument("--router", choices=["heuristic", "codex"], default="heuristic")
        command.add_argument("--planner", choices=["template", "codex"], default="template")
        command.add_argument("--codex-command", default="codex")
        command.add_argument("--codex-arg", action="append", default=[])

    submit = subparsers.add_parser("submit", help="Create a ticket from a natural-language request.")
    add_ticket_args(submit)
    route = subparsers.add_parser("route", help="Create a ticket and plan it immediately unless a PR lock is active.")
    add_ticket_args(route)

    subparsers.add_parser("status", help="Print numeric dashboard summary.")
    dashboard = subparsers.add_parser("dashboard", help="Print detailed dashboard.")
    dashboard.add_argument("--watch", action="store_true")
    dashboard.add_argument("--interval", type=float, default=5.0)

    plan = subparsers.add_parser("plan", help="Create a plan directory and queue from a ticket.")
    plan.add_argument("--ticket", type=Path, help="Ticket Markdown path. Defaults to first inbox ticket.")

    run_next = subparsers.add_parser("run-next", help="Generate or execute the next implementer unit.")
    run_next.add_argument("--plan", type=Path, required=True)
    run_next.add_argument("--dry-run", action="store_true")
    run_next.add_argument("--execute", action="store_true", help="Run Codex CLI for the selected unit.")
    run_next.add_argument("--commit", action="store_true", help="Commit changed files after successful execution.")
    run_next.add_argument("--codex-command", default="codex")
    run_next.add_argument("--codex-arg", action="append", default=[])
    run_next.add_argument("--allow-dirty", action="store_true")
    run_next.add_argument("--no-branch", action="store_true")
    run_next.add_argument("--auto-resolve", action="store_true", help="Auto-preserve dirty worktree state and continue when safe.")

    run_all = subparsers.add_parser("run-all", help="Generate or execute multiple ready units.")
    run_all.add_argument("--plan", type=Path, required=True)
    run_all.add_argument("--max-units", type=int, default=4)
    run_all.add_argument("--dry-run", action="store_true")
    run_all.add_argument("--execute", action="store_true")
    run_all.add_argument("--commit", action="store_true")
    run_all.add_argument("--codex-command", default="codex")
    run_all.add_argument("--codex-arg", action="append", default=[])
    run_all.add_argument("--allow-dirty", action="store_true")
    run_all.add_argument("--no-branch", action="store_true")
    run_all.add_argument("--auto-resolve", action="store_true", help="Auto-preserve dirty worktree state and continue when safe.")
    run_all.add_argument("--merge", action="store_true", help="Merge after all units are done.")
    run_all.add_argument("--target", default="main")
    run_all.add_argument("--remote", action="store_true", help="Use remote PR/merge mode for finalize steps.")
    run_all.add_argument("--open-pr", action="store_true", help="Create a PR dry-run or remote PR after all units are done.")

    mark = subparsers.add_parser("mark", help="Update a queue unit status.")
    mark.add_argument("--plan", type=Path, required=True)
    mark.add_argument("--unit", required=True)
    mark.add_argument("--status", required=True, choices=sorted(state.UNIT_STATUSES))

    subparsers.add_parser("morning-brief", help="Write today's morning review brief.")

    review = subparsers.add_parser("review", help="Write a review checklist for a plan.")
    review.add_argument("--plan", type=Path, required=True)

    open_pr = subparsers.add_parser("open-pr", help="Write a PR dry-run artifact or create a remote draft PR.")
    open_pr.add_argument("--plan", type=Path, required=True)
    open_pr.add_argument("--dry-run", action="store_true", default=True)
    open_pr.add_argument("--remote", action="store_true", help="Create a real remote draft PR with gh.")
    open_pr.add_argument("--ready", action="store_true", help="Create a ready PR instead of draft when using --remote.")
    open_pr.add_argument("--auto-resolve", action="store_true", help="Run unfinished units before creating the PR artifact.")
    open_pr.add_argument("--execute-units", action="store_true", help="Use Codex CLI while auto-resolving unfinished units.")
    open_pr.add_argument("--commit", action="store_true", help="Commit auto-resolved unit changes.")
    open_pr.add_argument("--max-units", type=int, default=8)
    open_pr.add_argument("--codex-command", default="codex")
    open_pr.add_argument("--codex-arg", action="append", default=[])
    open_pr.add_argument("--allow-dirty", action="store_true")
    open_pr.add_argument("--no-branch", action="store_true")

    pr_check = subparsers.add_parser("pr-check", help="Check PR lock state.")
    pr_check.add_argument("--gh-command", default="gh")
    subparsers.add_parser("drain", help="Route one inbox ticket when no PR lock is active.")

    set_lock = subparsers.add_parser("set-pr-lock", help="Create or replace the PR lock.")
    set_lock.add_argument("--branch", required=True)
    set_lock.add_argument("--pr-url", required=True)
    set_lock.add_argument("--status", default="reviewing")

    subparsers.add_parser("clear-pr-lock", help="Remove the active PR lock.")

    merge = subparsers.add_parser("merge", help="Merge only with explicit --execute.")
    merge.add_argument("--plan", type=Path)
    merge.add_argument("--target", default="main")
    merge.add_argument("--execute", action="store_true")
    merge.add_argument("--remote", action="store_true")
    merge.add_argument("--auto-resolve", action="store_true", help="Auto-complete unfinished units and treat the merge request as executable.")
    merge.add_argument("--execute-units", action="store_true", help="Use Codex CLI while auto-resolving unfinished units.")
    merge.add_argument("--commit", action="store_true", help="Commit auto-resolved unit changes.")
    merge.add_argument("--max-units", type=int, default=8)
    merge.add_argument("--codex-command", default="codex")
    merge.add_argument("--codex-arg", action="append", default=[])
    merge.add_argument("--allow-dirty", action="store_true")
    merge.add_argument("--no-branch", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        flow = state.ensure_initialized(args.repo)
        print(f"initialized: {flow.root}")
        return 0

    if args.command in {"submit", "route"}:
        ticket = tickets.submit_ticket(
            args.title,
            repo=args.repo,
            priority=args.priority,
            project=args.project,
            allow_draft_pr=args.allow_draft_pr,
            no_implement=args.no_implement,
        )
        print(f"ticket_created: {ticket.path}")
        if args.command == "route":
            lock = pr.read_pr_lock(args.repo)
            if lock:
                inbox.append_inbox_request(args.repo, ticket.title, "PR lock is active.")
                tickets.update_ticket_status(ticket.path, "inbox")
                print("route: queued due to active PR lock")
            elif args.plan:
                request_path = plans.append_plan_request(args.plan, ticket.title, reason=args.reason)
                tickets.update_ticket_status(ticket.path, "queued")
                print(f"route_to_existing_plan: {request_path}")
            else:
                repo = state.resolve_repo(args.repo)
                decision = route_decision(args, ticket.title, lock)
                if decision.action == "pause_for_pr_review":
                    inbox.append_inbox_request(repo, ticket.title, decision.reason)
                    tickets.update_ticket_status(ticket.path, "inbox")
                    print("route: queued due to router decision")
                    return 0
                if decision.action == "existing_plan":
                    request_path = plans.append_plan_request(decision.plan_path, ticket.title, reason=decision.reason)
                    tickets.update_ticket_status(ticket.path, "queued")
                    print(f"route_to_existing_plan: {request_path}")
                    return 0
                planner = CodexPlannerAgent(command=args.codex_command, extra_args=args.codex_arg) if args.planner == "codex" else TemplatePlannerAgent()
                plan = plans.create_plan_from_ticket(
                    ticket.path,
                    repo=args.repo,
                    branch_name=decision.branch_name or args.branch,
                    plan_title=decision.plan_title or args.plan_title,
                    planner=planner,
                    prepare_git_branch=True,
                    reason=decision.reason,
                )
                print(f"plan_created: {plan.plan_path}")
                print(f"queue_created: {plan.queue_md}")
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
        result = runner.run_next(
            args.plan,
            dry_run=args.dry_run,
            execute=args.execute,
            commit=args.commit,
            codex_command=args.codex_command,
            codex_args=args.codex_arg,
            allow_dirty=args.allow_dirty,
            no_branch=args.no_branch,
            auto_resolve=args.auto_resolve,
        )
        if result is None:
            print("no_ready_units")
            return 0
        print(f"unit: {result['unit']['id']}")
        print(f"prompt: {result['prompt_path']}")
        if args.dry_run:
            print("dry_run: prompt not written and queue not changed")
        elif args.execute:
            print(f"action: {result.get('action')}")
            if result.get("commit"):
                print(f"commit: {result['commit']}")
            if result.get("changed_paths"):
                print(f"changed_paths: {', '.join(result['changed_paths'])}")
            if result.get("auto_resolved_dirty"):
                print(f"auto_resolved_dirty: {', '.join(result['auto_resolved_dirty'])}")
        else:
            print("status: prompted")
        return 0

    if args.command == "run-all":
        run_result = RunAllRunner().run_all(
            args.plan,
            max_units=args.max_units,
            dry_run=args.dry_run,
            execute=args.execute,
            commit=args.commit,
            codex_command=args.codex_command,
            codex_args=args.codex_arg,
            allow_dirty=args.allow_dirty,
            no_branch=args.no_branch,
            auto_resolve=args.auto_resolve,
            open_pr=args.open_pr,
            merge=args.merge,
            remote=args.remote,
            target=args.target,
        )
        results = run_result.steps
        print(f"units_processed: {len(results)}")
        for result in results:
            suffix = f" {result.get('action')}" if result.get("action") else ""
            print(f"- {result['unit']['id']}: {result['prompt_path']}{suffix}")
        if run_result.message:
            print(run_result.message)
        return 0

    if args.command == "mark":
        unit = plans.mark_unit(args.plan, args.unit, args.status)
        print(f"unit_marked: {unit['id']} -> {unit['status']}")
        return 0

    if args.command == "morning-brief":
        brief = briefs.write_morning_brief(args.repo)
        print(f"morning_brief: {brief}")
        return 0

    if args.command == "review":
        review_path = briefs.write_review(args.plan)
        print(f"review: {review_path}")
        return 0

    if args.command == "open-pr":
        if args.auto_resolve:
            auto_complete_result = auto_complete_units(args)
            if auto_complete_result:
                print(auto_complete_result)
        if args.remote:
            url, lock_path = pr.create_remote_pr(args.plan, draft=not args.ready)
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
        message = pr.merge_plan(args.plan, target=args.target, remote=args.remote, execute=args.execute or args.auto_resolve)
        print(message)
        return 0 if not message.startswith("merge: hard-stop") and not message.startswith("merge: needs_work") else 2

    parser.print_help()
    return 1


def auto_complete_units(args: argparse.Namespace) -> str:
    plan_dir, queue_data = plans.load_queue(args.plan)
    queue_data = plan_readiness.sync_queue_cache_from_plan(plan_dir / "plan.md")
    unfinished_before = plans.unfinished_units(queue_data)
    if not unfinished_before:
        return ""
    requeued = plans.requeue_unfinished_units(args.plan, reason=f"auto-resolve before {args.command}")
    results = runner.run_all(
        args.plan,
        max_units=args.max_units,
        execute=args.execute_units,
        commit=args.commit,
        codex_command=args.codex_command,
        codex_args=args.codex_arg,
        allow_dirty=args.allow_dirty,
        no_branch=args.no_branch,
        auto_resolve=args.auto_resolve,
    )
    refreshed_dir, refreshed = plans.load_queue(args.plan)
    refreshed = plan_readiness.sync_queue_cache_from_plan(refreshed_dir / "plan.md")
    remaining = plans.unfinished_units(refreshed)
    return (
        "auto_resolve_units: "
        f"unfinished_before={len(unfinished_before)} "
        f"requeued={len(requeued)} "
        f"processed={len(results)} "
        f"remaining={len(remaining)}"
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
        url, lock_path = pr.create_remote_pr(args.plan, draft=True)
        return f"opened_pr: {url}\npr_lock: {lock_path}"
    pr_path = pr.write_pr_dry_run(args.plan)
    return f"pr_dry_run: {pr_path}"


def route_decision(args: argparse.Namespace, prompt: str, lock: Path | None) -> RouterAgentDecision:
    if args.branch or args.plan_title:
        title = args.plan_title or prompt.splitlines()[0][:80] or "User Request"
        return RouterAgentDecision("new_plan", args.reason, branch_name=args.branch or f"codex/{state.slugify(title)}", plan_title=title)
    repo = state.resolve_repo(args.repo)
    lock_text = lock.read_text(encoding="utf-8") if lock else None
    active_plans = collect_active_plans(repo)
    agent = CodexRouterAgent(command=args.codex_command, extra_args=args.codex_arg) if args.router == "codex" else HeuristicRouterAgent()
    return agent.decide(RouterAgentInput(repo=repo, prompt=prompt, pr_lock=lock_text, active_plans=active_plans))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
