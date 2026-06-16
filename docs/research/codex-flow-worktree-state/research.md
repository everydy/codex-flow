# Research

## Goal

Investigate whether Codex Flow should create and operate its `.codex-flow` tickets, queue, plans, prompts, logs, attempts, PR locks, and dashboard inside each task-specific worktree when `$구현커밋` runs. Also investigate a safe finalization model that merges completed work, preserves Codex Flow state, removes generated task worktrees, and closes finished branches without losing diagnostics or deleting user work.

The immediate operator decision is to rollback the earlier skill-only lane-discipline update and research a runtime-level design instead.

## Scope And Entry Points

- Starting mode: Pre-Plan Research Gate.
- Evidence lanes: codebase and reasoning.
- Target runtime repository: `/Users/moonsoo/projects/codex-flow`.
- Target operator-facing mirror skill: `/Users/moonsoo/projects/codex-flow/skills/구현커밋/SKILL.md`.
- Not covered yet: a concrete implementation plan, UI work, external package changes, or GitHub remote behavior.

## Relevant Files

- `/Users/moonsoo/projects/codex-flow/codex_flow/state.py`
- `/Users/moonsoo/projects/codex-flow/codex_flow/cli.py`
- `/Users/moonsoo/projects/codex-flow/codex_flow/tickets.py`
- `/Users/moonsoo/projects/codex-flow/codex_flow/plans.py`
- `/Users/moonsoo/projects/codex-flow/codex_flow/inbox.py`
- `/Users/moonsoo/projects/codex-flow/codex_flow/runner.py`
- `/Users/moonsoo/projects/codex-flow/codex_flow/git_ops.py`
- `/Users/moonsoo/projects/codex-flow/codex_flow/run_all.py`
- `/Users/moonsoo/projects/codex-flow/codex_flow/merge.py`
- `/Users/moonsoo/projects/codex-flow/codex_flow/source_plan.py`
- `/Users/moonsoo/projects/codex-flow/codex_flow/pr.py`
- `/Users/moonsoo/projects/codex-flow/codex_flow/dashboard.py`
- `/Users/moonsoo/projects/codex-flow/codex_flow/briefs.py`
- `/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py`
- `/Users/moonsoo/projects/codex-flow/scripts/implementation_commit.py`
- `/Users/moonsoo/projects/codex-flow/scripts/구현커밋.py`
- `/Users/moonsoo/projects/codex-flow/README.md`
- `/Users/moonsoo/projects/codex-flow/skills/구현커밋/SKILL.md`
- `/Users/moonsoo/projects/codex-flow/tests/test_state.py`
- `/Users/moonsoo/projects/codex-flow/tests/test_plans.py`
- `/Users/moonsoo/projects/codex-flow/tests/test_route_plan_first_source.py`
- `/Users/moonsoo/projects/codex-flow/tests/test_runner_brief.py`
- `/Users/moonsoo/projects/codex-flow/tests/test_agent_git_ops.py`
- `/Users/moonsoo/projects/codex-flow/tests/test_inbox_merge.py`
- Local command evidence from `git worktree -h`

## Current Behavior

Codex Flow currently treats the target repository root as both:

1. the git worktree where implementation commands run, and
2. the state root where `.codex-flow/` stores tickets, plans, queues, prompts, logs, attempts, locks, briefs, and dashboard files.

`state.paths(repo)` resolves a repository with `resolve_repo(repo)` and then sets:

```text
root = repo_path / ".codex-flow"
tickets = root / "tickets"
plans = root / "plans"
inbox = root / "inbox.md"
dashboard = root / "dashboard.md"
```

`route` creates plan artifacts under that same `.codex-flow/plans/<slug>/` tree. `run-next` and `run-all` then execute Codex in the repo inferred from the plan path. Many runtime paths therefore assume this shape:

```text
<repo>/.codex-flow/plans/<slug>/plan.md
```

The current README documents this exact shape and command style.

## Data Flow And Control Flow

Current route flow:

1. CLI parses `--repo` and `route <source_plan>`.
2. `source_plan.resolve_source_plan(value, repo)` resolves the plan-first source relative to `state.resolve_repo(repo)`.
3. `plans.create_plan_from_source(source, repo=args.repo, prepare_git_branch=True)` calls `state.ensure_initialized(repo)`.
4. `ensure_initialized(repo)` creates `repo/.codex-flow`.
5. `create_plan_from_source` writes:
   - top-level ticket under `repo/.codex-flow/tickets/`
   - source snapshot under `repo/.codex-flow/plans/<slug>/source-plan.md`
   - extracted tickets under `repo/.codex-flow/plans/<slug>/tickets/`
   - macro plan, `plan.md`, `queue.json`, `queue.md`, `log.md`, `decisions.md`, `artifacts.md`, `handoff.md`
6. If `prepare_git_branch=True`, `git_ops.prepare_branch(flow.repo, branch)` switches or creates the implementation branch in the same repo/worktree.

Current execution flow:

1. `runner.run_next(plan_path)` loads the queue from `plan_path`.
2. `runner.execute_unit(...)` computes `repo = plan_dir.parents[2]`.
3. Dirty checks, branch preparation, scoped status, Codex CLI execution, changed-path detection, and commits all run in that inferred repo.
4. Queue and log updates are written back to the same `plan_dir`.
5. `state.refresh_dashboard(plan_dir.parents[2])` refreshes the state rooted at the same repo.

Current finalization flow:

1. `pr.create_remote_pr(plan_path)` and `merge.MergeRunner` also infer `repo = plan_dir.parents[2]`.
2. PR locks live in `repo/.codex-flow/locks/pr-lock.md`.
3. Merge, push, and PR operations run against the same repo inferred from the plan path.
4. The current `merge_local` path already attempts local merge and `git branch -d` branch close, but it does not remove task worktrees or preserve task-local `.codex-flow` state before cleanup.

Current `구현커밋` skill state checked on 2026-06-16:

- Canonical installed skill: `/Users/moonsoo/projects/codex-skills-user/구현커밋/SKILL.md`
  - Dirty relative to its repo HEAD.
  - 192 lines, below the skill-creator 500-line guidance.
  - Has `Plan Source Auto-Discovery`, including automatic source-plan discovery from current input, previous final answers, active `.codex-flow`, recent plan-first files, and narrow candidate folders.
  - Mentions local merge and `git branch -d` branch close.
  - Does not mention task-specific worktree creation, task-local `.codex-flow`, archive, generated worktree cleanup, integration worktree merge, `cleanup_held`, `--keep-worktree`, or `--no-cleanup`.
- Canonical UI metadata: `/Users/moonsoo/projects/codex-skills-user/구현커밋/agents/openai.yaml`
  - YAML parses.
  - Still says `plan-first 구현, commit-unit 실행, 리뷰, 복구, PR 준비`.
  - Default prompt still says `ticket, plan/branch, run-next/run-all, commit-unit commit, morning brief, PR dry-run`.
  - It does not mention local merge, branch close, task worktrees, or cleanup.
- Codex Flow mirror skill: `/Users/moonsoo/projects/codex-flow/skills/구현커밋/SKILL.md`
  - Dirty relative to its repo HEAD.
  - 166 lines, below the skill-creator 500-line guidance.
  - Mentions local merge and `git branch -d` branch close.
  - Does not include canonical `Plan Source Auto-Discovery`.
  - Does not mention task-specific worktree creation, task-local `.codex-flow`, archive, generated worktree cleanup, integration worktree merge, `cleanup_held`, `--keep-worktree`, or `--no-cleanup`.

Skill-creator implications:

- The skill body is small enough to keep the new core workflow directly in `SKILL.md`; no new reference file is needed for this specific update.
- The frontmatter `description` must carry the trigger-relevant behavior because it is loaded before the body. It should mention task worktree execution and cleanup/finalization.
- `agents/openai.yaml` should be updated because skill-creator recommends keeping UI metadata aligned with SKILL.md.
- Avoid adding auxiliary README/changelog files to the skill package.
- Do not update the skill before runtime behavior exists. Documentation should trail passing runtime behavior to avoid promising behavior that the CLI cannot perform.

## Existing Abstractions And Boundaries

- `FlowPaths` is the central state path abstraction, but it currently stores only `repo` and state subpaths. It has no separate `state_root`, `execution_repo`, or `worktree` identity.
- `state.resolve_repo` discovers a git root from a path. It does not distinguish between a canonical project root, an isolated worktree root, and a central Codex Flow control workspace.
- Plan metadata in `queue.json` includes `branch`, `plan_slug`, source plan metadata, and unit metadata, but it does not include an explicit `worktree_path`, `execution_repo`, or `state_root`.
- Several modules bypass `FlowPaths` by deriving the repo from `plan_dir.parents[2]`. This is the main implementation boundary that would break if the state tree were moved outside the execution worktree without a metadata migration.
- `git_ops.dirty_paths` intentionally ignores `.codex-flow/` for changed-path and dirty-worktree decisions. This depends on state being inside the repo but excluded from implementation commits.
- `source_plan.check_source_drift` resolves relative source paths with `directory.parents[2]`, so source drift checks also depend on the current state layout.
- `merge.MergeRunner.close_source_branch` uses `git branch -d`, which is the right safety default for branch deletion because it fails when the branch is not safely merged or cannot be deleted.
- Git exposes `git worktree remove [-f] <worktree>` and `git worktree prune`. The safe default should call remove without `-f`, and treat failure as a held cleanup state instead of forcing deletion.
- Tests encode the current layout with assertions under `tmp_path/.codex-flow/...`.
- The user-facing skill surface is split: the installed canonical skill has newer auto-discovery language, while the codex-flow mirror does not. Any plan that changes skill behavior must update both surfaces or explicitly preserve a one-way canonical-to-mirror sync rule.

## Side Effects And Integration Points

- Moving `.codex-flow` out of the execution worktree without adding explicit metadata would break `runner`, `merge`, `pr`, `source_plan`, and `plans.mark_unit`.
- Keeping one central `.codex-flow` in a long-lived repo root can mix unrelated task queues, PR locks, dashboard state, and prompts across branches or worktrees.
- Putting `.codex-flow` inside each task worktree naturally scopes tickets, queue, plan, logs, and attempts to that unit of work, but dashboard and PR lock visibility become per-worktree unless the CLI gets a way to aggregate them.
- `route` currently prepares the git branch after creating the plan. If task-specific worktrees are introduced, worktree creation should happen before or during route so the state is born in the target worktree.
- `prepare_branch` currently uses `git switch` in the active repo. For worktree-first execution, branch preparation should use `git worktree add` for new task worktrees, and only use `git switch` inside an already selected task worktree.
- `run-next`, `run-all`, `open-pr`, `create-pr`, and `merge` accept a `--plan` path. The least surprising migration is to make the plan path itself identify the task worktree, while optionally storing explicit metadata to avoid fragile parent traversal.
- Removing a task worktree after completion is only safe after implementation changes are committed, the source branch is merged into the target, task-local `.codex-flow` state is archived outside the worktree, and `git worktree remove` succeeds without force.
- A target branch can be checked out in another worktree. Therefore local merge should not assume it can switch the task worktree or source root to `main`; a dedicated integration worktree is safer for merge/finalize.

## Risk To Surrounding Systems

- High risk: only updating `skills/구현커밋/SKILL.md` will not prevent recurrence. The runtime can still create queues and plans in whichever repo/worktree `--repo` points to.
- High risk: introducing a central external state directory such as `/Users/moonsoo/projects/codex-flow/.codex-flow-workspaces/...` without changing `parents[2]` assumptions would make the runtime operate on the wrong repo.
- Medium risk: per-task `.codex-flow` directories can reduce mixed branch state, but they may make global dashboard, inbox, and PR lock behavior less visible unless a later aggregate command is added.
- Medium risk: `git worktree add` behavior must handle existing branches, existing paths, dirty source roots, and branch naming collisions. The current `prepare_branch` is too simple for that.
- Medium risk: `source_plan` drift checks need to preserve the source plan path relative to the execution worktree or store an absolute/source-root-aware path.
- Medium risk: removing task worktrees without first archiving `.codex-flow` can lose prompts, logs, queue state, attempts, review diagnostics, and PR dry-run artifacts.
- Medium risk: deleting a branch before removing the worktree that has it checked out can fail. The safer order is merge target first, archive state, remove worktree, then `git branch -d`.
- Medium risk: using `git worktree remove -f` as a default can delete untracked or dirty files. Force removal should not be the default finalize path.
- Low risk: tests can cover the migration with temporary git repos and worktrees without package installation.

## Do Not Duplicate Or Bypass

- Do not duplicate state path logic in each command. Extend `state.FlowPaths` or add a related worktree/state resolver.
- Do not bypass `plans.load_queue` and `plans.save_queue`; they are the queue persistence boundary.
- Do not bypass `git_ops` for status, dirty path handling, branch creation, changed path detection, stashing, commits, push, and merge helpers.
- Do not rewrite the plan-first extraction flow. `plans.create_plan_from_source`, `plan_first_extract`, and `source_plan.snapshot_source_plan` should remain the route path.
- Do not remove `.codex-flow` compatibility names. README and skill docs treat `codex_flow`, `.codex-flow`, and `scripts/codex_flow.py` as runtime compatibility surfaces.
- Do not use `git worktree remove -f` or `git branch -D` as normal cleanup behavior. Safe cleanup should hold and report a reason instead of forcing deletion.
- Do not remove task-local `.codex-flow` state until a closeout archive has been written and verified.
- Do not let the codex-flow mirror skill drift from `/Users/moonsoo/projects/codex-skills-user/구현커밋/SKILL.md` on user-visible workflow rules.
- Do not update `agents/openai.yaml` with behavior not reflected in `SKILL.md`.

## Open Questions

- Should Codex Flow create the task worktree automatically during `route`, or should the skill create the worktree before invoking `route --repo <task_worktree>`?
- Should each task worktree contain its own `.codex-flow/`, or should there be a central state index plus per-task state directories?
- Should PR locks be per task worktree, or should there also be a repo-global lock/index to prevent multiple simultaneous PRs?
- Where should worktrees live by default: under `~/.config/superpowers/worktrees/<repo>/<slug>`, under the project root, or under a Codex Flow-managed directory?
- Should `--repo` continue to mean "execution worktree", while a new option such as `--control-repo` or `--worktree-root` controls where worktrees are created?
- Should the closeout archive live under `~/.config/superpowers/worktrees/<repo>/_archive/<slug>` or under a Codex Flow-specific cache directory?
- Should `run-all --auto-resolve` remove task worktrees by default after local merge, or should cleanup be controlled by `--cleanup-worktree` with a skill default that passes it?

## Worktree Finalization And Cleanup Research

Recommended safe close sequence:

1. Verify plan readiness: every unit is `done` and final gate has no blocker or important issue.
2. Verify task worktree implementation cleanliness: `dirty_paths(status(task_worktree), ignore_flow=True)` must be empty.
3. Merge the source branch into the target branch from a clean integration worktree, not by switching the task worktree or a possibly busy source root.
4. Verify merge containment: `git merge-base --is-ancestor <source_branch> <target_branch>` must pass.
5. Archive task-local `.codex-flow` state outside the task worktree.
6. Verify archive exists and includes at least `plan.md`, `queue.json`, `log.md`, `source.json` when present, and `attempts/` when present.
7. Remove the task worktree with plain `git worktree remove <task_worktree>`.
8. Delete the local source branch with `git branch -d <source_branch>`.
9. Optionally run `git worktree prune` only after successful remove, and only as housekeeping for stale worktree metadata.
10. Write closeout status to the archive and final CLI output.

Recommended hold conditions:

- plan is not complete
- non-`.codex-flow` dirty paths exist in the task worktree
- target merge fails or leaves conflicts
- merge containment check fails
- archive copy/verification fails
- plain `git worktree remove` fails
- `git branch -d` fails

Recommended defaults:

- `run-all --auto-resolve` should finalize by default when all units are done: local merge, state archive, task worktree remove, branch close.
- Add an escape hatch such as `--keep-worktree` or `--no-cleanup` for intentional inspection.
- Add a retry command/path such as `cleanup --plan <plan.md>` or `merge --auto-resolve` continuing cleanup when the merge already succeeded.
- Do not use force removal by default. If a future force option exists, it must be explicit and should still refuse when non-flow dirty paths exist.

Implementation implication:

- Add cleanup helpers to `git_ops`, but orchestrate closeout in `merge` or a new finalizer module because cleanup depends on plan readiness, target merge, branch close, and state archive.
- Store archive path and cleanup status in queue metadata so a later command can report or retry cleanup.
- Update `skills/구현커밋/SKILL.md` and the installed canonical skill so user-facing behavior says completed work is merged, state is archived, the generated worktree is removed, and the branch is closed only when safety gates pass.

## Plan Implications

Recommended direction:

1. Revert the previous skill-only lane discipline update. Runtime behavior should be fixed before documenting stronger guarantees in the skill.
2. Treat `--repo` as the execution worktree root. When `$구현커밋` starts from a plan-first source and no task worktree exists, create a task worktree first and run `route --repo <task_worktree>` there.
3. Keep `.codex-flow/` inside the task worktree for tickets, plan, queue, prompts, logs, attempts, and task-local PR artifacts.
4. Add explicit metadata to queue or source metadata:
   - `execution_repo`
   - `worktree_path`
   - `source_repo`
   - `source_plan_path`
5. Replace `plan_dir.parents[2]` with a resolver such as `state.repo_for_plan(plan_dir)` that first reads metadata and falls back to the legacy parent layout.
6. Add `git_ops.ensure_worktree(...)` or equivalent to create or reuse task worktrees safely.
7. Add safe finalization cleanup:
   - merge through a clean integration worktree
   - archive `.codex-flow` state outside the task worktree
   - remove the generated task worktree without force
   - close the source branch with `git branch -d`
   - hold and report when any safety gate fails
8. Update skill documents after the runtime behavior exists:
   - update canonical `구현커밋/SKILL.md` frontmatter description and body
   - sync the codex-flow mirror skill with canonical workflow rules, including Plan Source Auto-Discovery
   - update canonical `agents/openai.yaml`
   - keep the skill concise and avoid new auxiliary files
9. Update tests before implementation:
   - route creates or uses a task worktree
   - plan artifacts are written under `<task_worktree>/.codex-flow`
   - run-next executes in `<task_worktree>`
   - source drift still resolves the original source plan
   - PR lock behavior is clearly per-worktree or aggregated by an explicit index
   - completed run-all archives state, removes generated task worktree, and closes branch only after merge containment

Non-recommended direction:

- A pure skill-document rule that says "use lane worktrees" without runtime enforcement.
- A central external state store without first replacing all `parents[2]` repo inference.
- Continuing to stash mixed dirty states as the main prevention mechanism.
- Removing generated worktrees with `git worktree remove -f` as the normal path.

## Evidence

- `state.paths(repo)` sets `root = repo_path / ".codex-flow"` in `/Users/moonsoo/projects/codex-flow/codex_flow/state.py`.
- `tickets.submit_ticket` writes to `flow.tickets` and appends `flow.inbox` in `/Users/moonsoo/projects/codex-flow/codex_flow/tickets.py`.
- `plans.create_plan_from_source` creates top-level tickets, source snapshots, extracted tickets, plan, queue, log, decisions, artifacts, and handoff under `flow.plans` in `/Users/moonsoo/projects/codex-flow/codex_flow/plans.py`.
- `runner.execute_unit` infers `repo = plan_dir.parents[2]` before dirty checks, branch preparation, Codex execution, changed-path detection, and commits in `/Users/moonsoo/projects/codex-flow/codex_flow/runner.py`.
- `merge.MergeRunner` and `pr.create_remote_pr` infer `repo = plan_dir.parents[2]` in `/Users/moonsoo/projects/codex-flow/codex_flow/merge.py` and `/Users/moonsoo/projects/codex-flow/codex_flow/pr.py`.
- `source_plan.check_source_drift` resolves relative source paths from `directory.parents[2]` in `/Users/moonsoo/projects/codex-flow/codex_flow/source_plan.py`.
- `git_ops.dirty_paths` ignores `.codex-flow/` when deciding dirty paths in `/Users/moonsoo/projects/codex-flow/codex_flow/git_ops.py`.
- `merge.MergeRunner.close_source_branch` uses `delete_local_branch`, which calls `git branch -d`, in `/Users/moonsoo/projects/codex-flow/codex_flow/merge.py` and `/Users/moonsoo/projects/codex-flow/codex_flow/git_ops.py`.
- `RunAllRunner.run_all` already routes completed executed runs into local merge by default when `execute` is true and `no_merge` is false in `/Users/moonsoo/projects/codex-flow/codex_flow/run_all.py`.
- `git worktree -h` confirms the available local commands include `add`, `list`, `remove [-f]`, `prune`, and `repair`.
- `wc -l` showed `/Users/moonsoo/projects/codex-skills-user/구현커밋/SKILL.md` has 192 lines and `/Users/moonsoo/projects/codex-flow/skills/구현커밋/SKILL.md` has 166 lines.
- `rg` confirmed `Plan Source Auto-Discovery` exists in canonical `/Users/moonsoo/projects/codex-skills-user/구현커밋/SKILL.md` but not in `/Users/moonsoo/projects/codex-flow/skills/구현커밋/SKILL.md`.
- Ruby YAML parsing succeeded for both `SKILL.md` frontmatters and `/Users/moonsoo/projects/codex-skills-user/구현커밋/agents/openai.yaml`.
- README documents `.codex-flow/plans/<slug>/` as the plan progress layout and describes `.codex-flow/` as the local planning state directory.
- Tests read and assert `tmp_path/.codex-flow/...` in `/Users/moonsoo/projects/codex-flow/tests/test_state.py`, `/Users/moonsoo/projects/codex-flow/tests/test_plans.py`, and `/Users/moonsoo/projects/codex-flow/tests/test_route_plan_first_source.py`.
- Command evidence gathered on 2026-06-16:
  - `rg --files`
  - `rg -n "parents\\[2\\]|state\\.paths\\(|ensure_initialized\\(|require_initialized\\(|resolve_repo\\(|FLOW_DIR|flow\\.root|flow\\.tickets|flow\\.plans|flow\\.inbox|flow\\.dashboard" codex_flow tests README.md skills/구현커밋/SKILL.md`
  - targeted `sed -n` reads for the files listed above.
