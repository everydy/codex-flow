# Codex Flow Task Worktree State Plan

## Goal

Codex Flow should prevent long-lived branch/state accumulation by creating or reusing a task-specific git worktree before `$구현커밋` routes a plan-first document. The task worktree should own its own `.codex-flow/` state, including tickets, plans, queues, prompts, logs, attempts, briefs, and task-local PR artifacts.

The plan changes runtime behavior, not only skill documentation.

When the implementation finishes, Codex Flow should also safely close the generated task worktree: merge completed work into the target branch, archive task-local `.codex-flow` state, remove the generated task worktree without force, and close the completed branch with `git branch -d` only when safety checks pass.

## Current Context

Research artifact:

- [Research](../research/codex-flow-worktree-state/research.md)
- [Review All-In-One](../research/codex-flow-worktree-state/review-all-in-one.md)

Confirmed current behavior:

- `state.paths(repo)` creates state under `repo/.codex-flow`.
- `route` writes tickets and plans under that same state root.
- `runner`, `merge`, `pr`, `source_plan`, and `plans.mark_unit` infer the execution repo with `plan_dir.parents[2]`.
- `git_ops.prepare_branch` switches branches in the active repo instead of creating task worktrees.
- `git_ops.dirty_paths` ignores `.codex-flow/`, which currently depends on state being inside the execution repo.
- Current `merge_local` already attempts local merge and branch close with `git branch -d`, but it does not archive task-local `.codex-flow` state or remove generated worktrees.

Confirmed current skill state:

- Canonical installed skill: `/Users/moonsoo/projects/codex-skills-user/구현커밋/SKILL.md`
  - Current dirty version already has local merge and branch close language.
  - Current dirty version has `Plan Source Auto-Discovery`.
  - It still describes the flow as `ticket -> plan/branch`, not `ticket -> task worktree -> plan/queue`.
  - It does not describe task-local `.codex-flow`, archive, non-force worktree removal, integration worktree merge, cleanup hold states, or cleanup retry.
- Codex Flow mirror skill: `/Users/moonsoo/projects/codex-flow/skills/구현커밋/SKILL.md`
  - Current dirty version has local merge and branch close language.
  - It is missing canonical `Plan Source Auto-Discovery`.
  - It also lacks task worktree and cleanup language.
- Canonical agent metadata: `/Users/moonsoo/projects/codex-skills-user/구현커밋/agents/openai.yaml`
  - Still says `plan-first 구현, commit-unit 실행, 리뷰, 복구, PR 준비`.
  - Default prompt still says `ticket, plan/branch, run-next/run-all, commit-unit commit, morning brief, PR dry-run`.
  - It is stale once runtime worktree/finalization behavior lands.

## In Scope

- Add a task-worktree-aware route path.
- Keep `.codex-flow/` inside each task worktree.
- Add explicit plan/queue metadata for execution and source paths.
- Replace fragile `plan_dir.parents[2]` usage with a resolver.
- Add safe worktree creation/reuse helpers.
- Add safe finalize cleanup: integration merge, `.codex-flow` archive, non-force task worktree removal, and branch close.
- Update tests, README, and `skills/구현커밋/SKILL.md`.

## Out Of Scope

- Remote GitHub workflow redesign.
- Global dashboard aggregation across all task worktrees.
- Repo-global PR lock/index.
- Force cleanup of dirty worktrees.
- UI or browser verification.
- Dependency installation or package upgrade.

## 검토용 결과물

- 계획 MD: this file.
- Review artifact: [Review All-In-One](../research/codex-flow-worktree-state/review-all-in-one.md)
- Research artifact: [Research](../research/codex-flow-worktree-state/research.md)
- HTML artifact: omitted.

## HTML 생략 보고서

HTML 검토물은 생략한다. 이번 작업은 CLI/runtime state routing, git worktree handling, metadata, tests, and docs 중심이다. 화면, 인터랙션, 디자인 판단이 없으므로 계획 MD와 검증 명령이 적절한 검토 표면이다.

## Operator 결정 필요 사항

상태: 없음. 적용한 기본값은 아래와 같다.

- 결정 제목: route 단계 worktree 자동 생성
  - 맥락: 사용자는 추천안대로 진행한다고 승인했다.
  - A: skill이 먼저 worktree를 만들고 route를 호출한다.
  - B: Codex Flow `route`가 task worktree를 자동 생성한다.
  - C: 사용자가 항상 `--repo <task_worktree>`를 직접 넘긴다.
  - 추천안: B
  - 기본값: B
  - 보류 시 영향: 자동 재발방지가 약해지고 스킬/사람 실수에 의존한다.

- 결정 제목: worktree 기본 위치
  - 맥락: 원본 repo를 더럽히지 않고 task state를 분리해야 한다.
  - A: `~/.config/superpowers/worktrees/<repo-name>/<slug>`
  - B: project root 아래 `.worktrees/<slug>`
  - C: Codex Flow repo 내부 관리 디렉터리
  - 추천안: A
  - 기본값: A
  - 보류 시 영향: task worktree 생성 규칙이 흔들리고 중복 경로가 생긴다.

- 결정 제목: PR lock 범위
  - 맥락: per-task state를 도입하면 lock도 task-local이 된다.
  - A: task-local PR lock만 유지한다.
  - B: repo-global PR lock/index를 이번 계획에 포함한다.
  - C: remote GitHub state만 신뢰한다.
  - 추천안: A
  - 기본값: A
  - 보류 시 영향: global aggregation은 후속 과제로 남지만 이번 변경 범위가 작아진다.

- 결정 제목: 완료 후 worktree cleanup 기본값
  - 맥락: 사용자는 구현커밋 완료 뒤 생성된 worktree를 없애거나 병합하는 안전한 로직을 원한다.
  - A: 완료 후 local merge, `.codex-flow` archive, task worktree remove, branch close를 기본 수행한다.
  - B: 완료 후 merge와 branch close만 하고 worktree는 남긴다.
  - C: cleanup은 별도 수동 명령에서만 수행한다.
  - 추천안: A
  - 기본값: A
  - 보류 시 영향: 작업별 worktree가 계속 남아 장기 누적 문제가 다시 생긴다.

## Proposed Runtime Contract

After this plan:

```text
source repo/worktree
  docs/plans/source-plan.md

task worktree
  .codex-flow/
    tickets/
    plans/<slug>/
      plan.md
      queue.json
      queue.md
      source-plan.md
      source.json
      macro-plan.md
      prompts/
      attempts/
      log.md

archive root
  <repo-name>/<slug>/
    plan.md
    queue.json
    log.md
    source.json
    attempts/
    closeout.json
```

`route` should:

1. Resolve the original source plan from the current repo or explicit `--repo`.
2. Compute a stable slug and branch.
3. Create or reuse a task worktree at the configured default root.
4. Run state initialization in that task worktree.
5. Create `.codex-flow` tickets, plan, queue, and source snapshots inside the task worktree.
6. Store metadata so later commands do not need to infer repo from `parents[2]`.

`run-all --auto-resolve` should finalize completed plans:

1. Confirm every unit is done and the review gate passed.
2. Confirm the task worktree has no dirty non-`.codex-flow` paths.
3. Merge the task branch into the target branch from a clean integration worktree.
4. Confirm the source branch is contained in the target branch.
5. Archive task-local `.codex-flow` state outside the task worktree.
6. Remove the generated task worktree with plain `git worktree remove <path>`.
7. Close the completed branch with `git branch -d <branch>`.
8. Hold cleanup, instead of forcing deletion, if any safety gate fails.

## Proposed Metadata

Illustrative shape:

```json
{
  "plan_slug": "codex-flow-task-worktree-state",
  "branch": "codex/codex-flow-task-worktree-state",
  "execution_repo": "/Users/moonsoo/.config/superpowers/worktrees/codex-flow/codex-flow-task-worktree-state",
  "worktree_path": "/Users/moonsoo/.config/superpowers/worktrees/codex-flow/codex-flow-task-worktree-state",
  "integration_worktree_path": "/Users/moonsoo/.config/superpowers/worktrees/codex-flow/_integrate/codex-flow-task-worktree-state-main",
  "source_repo": "/Users/moonsoo/projects/codex-flow",
  "source_plan_path": "/Users/moonsoo/projects/codex-flow/docs/plans/2026-06-16-codex-flow-task-worktree-state-plan.md",
  "source_plan_sha256": "...",
  "archive_path": "/Users/moonsoo/.config/superpowers/worktrees/codex-flow/_archive/codex-flow-task-worktree-state",
  "cleanup_status": "pending|archived|worktree_removed|branch_closed|held"
}
```

## Proposed Resolver

Illustrative API:

```python
def repo_for_plan(plan_dir: str | Path) -> Path:
    directory = Path(plan_dir).expanduser().resolve()
    metadata = read_plan_metadata(directory)
    if metadata.get("execution_repo"):
        return Path(metadata["execution_repo"]).expanduser().resolve()
    return directory.parents[2]
```

This keeps legacy plans working while allowing task worktree plans to carry explicit execution metadata.

## Safe Finalization Contract

The default completed `run-all --auto-resolve` path should become:

```text
all units done
  -> final review gate passed
  -> clean task worktree outside .codex-flow
  -> merge task branch into target from integration worktree
  -> verify branch is ancestor of target
  -> archive .codex-flow state
  -> git worktree remove <task-worktree>
  -> git branch -d <task-branch>
  -> git worktree prune
```

Safety rules:

- Never use `git worktree remove -f` as the default.
- Never use `git branch -D` as the default.
- Do not remove the task worktree while non-`.codex-flow` dirty paths exist.
- Do not remove `.codex-flow` state until the archive copy is verified.
- If cleanup fails after merge, keep the worktree and branch and report `cleanup_held` with the exact reason.
- Add `--keep-worktree` or `--no-cleanup` for intentional inspection.
- Add a retryable cleanup path, either `cleanup --plan <plan.md>` or `merge --auto-resolve`, for plans already merged but not cleaned.

## Skill-Creator Based Skill Update Contract

Use `skill-creator` for this skill update. The goal is not to make the skill longer; it is to make the trigger metadata and loaded workflow match the implemented runtime.

Skill-creator constraints to apply:

- Keep `SKILL.md` concise. The current canonical and mirror files are under 500 lines, so the task-worktree and cleanup workflow can live in `SKILL.md` without adding a new reference file.
- Put trigger-relevant behavior in frontmatter `description`: task worktree execution, commit-unit orchestration, safe finalize cleanup, review/repair, PR/merge readiness.
- Update `agents/openai.yaml` after reading the final SKILL.md so UI metadata matches the actual workflow.
- Do not add auxiliary README, changelog, or quick-reference files to the skill package.
- Do not add scripts/assets/references for this skill update unless runtime implementation proves repeated deterministic logic belongs inside the skill package. The runtime logic belongs in `/Users/moonsoo/projects/codex-flow`, not the skill folder.

Canonical skill changes required after runtime support exists:

1. Frontmatter description:
   - Include `task worktree`, `task-local .codex-flow`, `safe finalize cleanup`, `archive`, `worktree removal`, and `branch close`.
   - Keep existing trigger surface: plan-first implementation, commit-unit execution, review, repair, PR dry-run, PR creation, merge readiness, morning briefs.
   - Do not add new frontmatter fields. Existing local `metadata` can be preserved for compatibility, but do not expand it.

2. Purpose:
   - Change the flow from `ticket -> plan/branch -> ... -> local merge -> branch close` to `ticket/source plan -> task worktree -> task-local .codex-flow plan/queue -> run-next/run-all -> review gate -> commit -> safe finalize cleanup -> morning brief`.
   - State that the skill docs describe behavior only after the runtime commits are implemented.

3. Canonical Runtime:
   - Keep `/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py` as the only backend.
   - Add that route creates/reuses a task worktree and writes `.codex-flow` inside that task worktree.
   - Preserve `codex_flow`, `.codex-flow`, and `scripts/codex_flow.py` compatibility names.

4. Core Flow:
   - Step 1 should no longer say "initialize in repository root" as the normal path.
   - Route should detect the plan-first source, create/reuse the task worktree, initialize task-local `.codex-flow`, and create the queue there.
   - Run-next/run-all should execute from the task worktree plan path.
   - Completed run-all should finalize through local merge, archive, non-force worktree remove, and `git branch -d`.
   - Hold states should be named: `source_drift`, `human_gate`, `needs_work`, `cleanup_held`, `pr_locked`.

5. Plan Source Auto-Discovery:
   - Keep the canonical auto-discovery section.
   - Sync it into the codex-flow mirror skill.
   - Add that automatically discovered source plans are routed into a task worktree; the discovered source file itself is not moved.

6. Shortcut Commands:
   - `$구현커밋` should keep source/active plan auto-discovery, not fall back to status.
   - `$구현커밋 모두실행` should say it runs units, merges when complete, archives state, removes the generated task worktree when safe, and closes the branch.
   - Add or document cleanup retry semantics if the runtime exposes `cleanup --plan <plan.md>` or `merge --auto-resolve`.

7. Guardrails:
   - Never use `git worktree remove -f` or `git branch -D` in normal cleanup.
   - Do not remove a task worktree with non-`.codex-flow` dirty paths.
   - Do not remove task-local `.codex-flow` until archive verification passes.
   - If cleanup cannot finish, report `cleanup_held` and keep the worktree.
   - Keep `--keep-worktree` or `--no-cleanup` as an explicit inspection escape hatch.

8. Auto-Resolve Policy:
   - Add rows for:
     - generated worktree remains after completion
     - `.codex-flow` diagnostics would be lost
     - cleanup partially fails
   - Each row should prefer archive/hold/retry over force deletion.

9. Commands:
   - Add examples for task worktree paths once runtime exposes them.
   - Add `--keep-worktree` or `--no-cleanup`.
   - Add cleanup retry command if implemented.

10. Agent metadata:
   - `short_description` should mention lane/worktree/finalize cleanup within 25-64 chars if possible.
   - `default_prompt` must explicitly invoke `$구현커밋` and mention task worktree execution and safe finalize cleanup.
   - Keep strings quoted.

Mirror sync rule:

- Treat `/Users/moonsoo/projects/codex-skills-user/구현커밋/SKILL.md` as canonical.
- Update `/Users/moonsoo/projects/codex-flow/skills/구현커밋/SKILL.md` in the same commit or a directly adjacent docs commit.
- The mirror should not remain behind on Plan Source Auto-Discovery or finalize cleanup language.

Validation for the skill update:

- `git diff --check`
- Ruby YAML parse for both `SKILL.md` frontmatters and `agents/openai.yaml`
- `wc -l` to confirm `SKILL.md` stays comfortably under 500 lines
- `rg -n "task worktree|task-local|archive|cleanup_held|git worktree remove|git branch -d|Plan Source Auto-Discovery|keep-worktree|no-cleanup"` across canonical and mirror skill files
- Inspect `agents/openai.yaml` to ensure `default_prompt` starts with or clearly includes `$구현커밋`

## Skill Routing Manifest

| Phase | Required skills | Optional skills | Evidence |
| --- | --- | --- | --- |
| Commit 1: Add plan metadata and repo resolver | `plan-first-implementation`, `mission-completion-harness` | 없음 | `runner`, `merge`, `pr`, `source_plan`, and `plans.mark_unit` depend on `plan_dir.parents[2]`; introduce a resolver before changing behavior. |
| Commit 2: Add task worktree creation and route integration | `plan-first-implementation`, `mission-completion-harness` | 없음 | `git_ops.prepare_branch` currently switches branches in-place; route needs `git worktree add` or safe reuse. |
| Commit 3: Move execution and finalization callers to the resolver | `plan-first-implementation`, `mission-completion-harness` | 없음 | `run-next`, `run-all`, PR, merge, source drift, and mark paths must all use the same execution repo. |
| Commit 4: Update tests and smoke coverage | `plan-first-implementation`, `review-all-in-one` | `qa-gate` | Behavior must prove plan state is created under the task worktree and execution happens there. |
| Commit 5: Add safe finalize cleanup for generated worktrees | `plan-first-implementation`, `mission-completion-harness` | `review-all-in-one` | Completed plans must merge, archive state, remove generated worktrees, and close branches only after safety gates pass. |
| Commit 6: Update README and 구현커밋 skill docs | `plan-first-implementation`, `skill-creator` | `content-sync-auditor` | Runtime behavior must be reflected in canonical skill, mirror skill, UI metadata, and README after tests pass. |
| Final Gate | `review-all-in-one`, `qa-gate` | `테스트` | Final review must check runtime behavior, docs consistency, and available verification under the supply-chain freeze. |

## Implementation Plan

### Commit 1: Add plan metadata and repo resolver

- 대상 파일:
  - `codex_flow/state.py`
  - `codex_flow/plans.py`
  - `codex_flow/source_plan.py`
  - `tests/test_state.py`
  - `tests/test_plans.py`
- 변경:
  - Add a metadata reader/writer for plan directories.
  - Add `state.repo_for_plan(plan_dir)` or equivalent.
  - Store `execution_repo`, `worktree_path`, `source_repo`, `source_plan_path`, and source hash in `queue.json` or a plan-local metadata file.
  - Preserve legacy fallback to `plan_dir.parents[2]`.
- 코드 스니펫:

```python
def repo_for_plan(plan_dir: str | Path) -> Path:
    directory = Path(plan_dir).expanduser().resolve()
    metadata = read_plan_metadata(directory)
    execution_repo = metadata.get("execution_repo")
    if execution_repo:
        return Path(execution_repo).expanduser().resolve()
    return directory.parents[2]
```

- 검증:
  - `python3 -m py_compile codex_flow/state.py codex_flow/plans.py codex_flow/source_plan.py`
  - Existing package-backed pytest commands are deferred until the supply-chain freeze allows them or the operator explicitly authorizes that class of command.
- 성공 기준:
  - Legacy plans without metadata still resolve to the old repo.
  - New plans can resolve execution repo from metadata.
  - Source drift can read source metadata without assuming `parents[2]`.
- 중단 조건:
  - Existing plan loading or queue sync fails for legacy `.codex-flow/plans/<slug>/plan.md`.

### Commit 2: Add task worktree creation and route integration

- 대상 파일:
  - `codex_flow/git_ops.py`
  - `codex_flow/cli.py`
  - `codex_flow/plans.py`
  - `tests/test_route_plan_first_source.py`
- 변경:
  - Add `ensure_worktree(source_repo, branch, worktree_path)` with safe reuse.
  - Add route options only if needed, such as `--worktree-root`, while keeping default behavior automatic.
  - During `route`, create or reuse the task worktree before writing `.codex-flow`.
  - Run `state.ensure_initialized(task_worktree)` and write plan artifacts inside the task worktree.
  - Avoid `git switch` in the original root.
- 코드 스니펫:

```python
def ensure_worktree(source_repo: Path, branch: str, worktree_path: Path) -> Path:
    if worktree_path.exists():
        return worktree_path.resolve()
    result = run_process(["git", "worktree", "add", "-b", branch, str(worktree_path), "HEAD"], cwd=source_repo)
    if result.status != 0:
        raise SystemExit(command_failure("git worktree add failed", result))
    return worktree_path.resolve()
```

- 검증:
  - `python3 -m py_compile codex_flow/git_ops.py codex_flow/cli.py codex_flow/plans.py`
  - CLI smoke with a temporary git repo may be run with stdlib and local scripts only.
- 성공 기준:
  - `route` leaves the source worktree branch unchanged.
  - `.codex-flow/plans/<slug>/plan.md` is created inside the task worktree.
  - Queue metadata records source and execution paths.
- 중단 조건:
  - Existing branch already belongs to another worktree and safe reuse cannot be proven.

### Commit 3: Move execution and finalization callers to the resolver

- 대상 파일:
  - `codex_flow/runner.py`
  - `codex_flow/merge.py`
  - `codex_flow/pr.py`
  - `codex_flow/source_plan.py`
  - `codex_flow/plans.py`
  - `tests/test_runner_brief.py`
  - `tests/test_inbox_merge.py`
- 변경:
  - Replace all `plan_dir.parents[2]` execution repo inference with `state.repo_for_plan(plan_dir)`.
  - Update source drift to use `source_repo` and `source_plan_path` metadata first.
  - Keep fallback for legacy plans.
  - Ensure `run-next`, `run-all`, PR dry-run, PR create, merge, and mark all operate against the same execution repo.
- 코드 스니펫:

```python
repo = state.repo_for_plan(plan_dir)
state.refresh_dashboard(repo)
```

- 검증:
  - `python3 -m py_compile codex_flow/runner.py codex_flow/merge.py codex_flow/pr.py codex_flow/source_plan.py codex_flow/plans.py`
  - Package-backed tests deferred under the active supply-chain freeze unless explicitly allowed.
- 성공 기준:
  - No runtime caller still depends on `parents[2]` for execution repo when metadata exists.
  - Legacy fallback remains intact.
  - Source drift checks the original source file, not the copied `source-plan.md`.
- 중단 조건:
  - A command cannot determine whether it should operate on source repo or execution worktree.

### Commit 4: Update tests and smoke coverage

- 대상 파일:
  - `tests/test_state.py`
  - `tests/test_route_plan_first_source.py`
  - `tests/test_runner_brief.py`
  - `tests/test_inbox_merge.py`
  - `tests/test_agent_git_ops.py`
- 변경:
  - Add tests for route-created task worktrees.
  - Assert plan artifacts are written under `<task_worktree>/.codex-flow`.
  - Assert source repo branch is not switched by route.
  - Assert run-next executes inside the task worktree.
  - Assert legacy plans still work.
- 코드 스니펫: 필요 없음. Test assertions are more useful than a proposed snippet here.
- 검증:
  - `python3 -m py_compile` for changed runtime files.
  - If allowed later, targeted pytest:
    - `python3 -m pytest tests/test_route_plan_first_source.py tests/test_runner_brief.py tests/test_inbox_merge.py tests/test_state.py`
- 성공 기준:
  - Test coverage proves the old mixed-root failure mode is no longer the default.
  - Test coverage proves legacy state still resolves.
- 중단 조건:
  - Package-backed pytest cannot be run and stdlib smoke cannot cover route/worktree behavior enough for confidence.

### Commit 5: Add safe finalize cleanup for generated worktrees

- 대상 파일:
  - `codex_flow/git_ops.py`
  - `codex_flow/merge.py`
  - `codex_flow/run_all.py`
  - `codex_flow/cli.py`
  - `codex_flow/plans.py`
  - `tests/test_inbox_merge.py`
  - `tests/test_runner_brief.py`
- 변경:
  - Add helpers for `git worktree list`, `git worktree remove`, `git worktree prune`, and merge containment checks.
  - Add a closeout archive step that copies task-local `.codex-flow` state to a durable archive path before removing the worktree.
  - Add cleanup metadata such as `archive_path`, `cleanup_status`, and `cleanup_reason`.
  - Run local merge from a clean integration worktree so the task worktree does not need to switch to the target branch.
  - After merge containment and archive verification, remove the task worktree without `-f`.
  - Delete the completed branch with `git branch -d` only after the task worktree is removed.
  - Add `--keep-worktree` or `--no-cleanup` as an escape hatch.
  - Add a retryable cleanup path for plans already merged but not cleaned.
- 코드 스니펫:

```python
def cleanup_generated_worktree(plan_dir: Path, branch: str, target: str) -> CleanupResult:
    task_repo = state.repo_for_plan(plan_dir)
    dirty = dirty_paths(status(task_repo), ignore_flow=True)
    if dirty:
        return CleanupResult("held", f"dirty paths: {', '.join(dirty)}")
    if not branch_is_ancestor(task_repo, branch, target):
        return CleanupResult("held", "branch is not contained in target")
    archive_path = archive_flow_state(plan_dir)
    if not archive_verified(archive_path):
        return CleanupResult("held", "archive verification failed")
    remove = remove_worktree(task_repo)
    if remove.status != 0:
        return CleanupResult("held", command_failure("git worktree remove failed", remove))
    delete = delete_local_branch(state.source_repo_for_plan(plan_dir), branch)
    if delete.status != 0:
        return CleanupResult("held", command_failure("git branch -d failed", delete))
    return CleanupResult("branch_closed", str(archive_path))
```

- 검증:
  - `python3 -m py_compile codex_flow/git_ops.py codex_flow/merge.py codex_flow/run_all.py codex_flow/cli.py codex_flow/plans.py`
  - CLI smoke with a temporary git repo:
    - route creates a task worktree
    - run-all completes
    - merge containment passes
    - archive path exists
    - task worktree is absent from `git worktree list --porcelain`
    - branch is absent from `git branch --list <branch>`
  - If allowed later, targeted pytest:
    - `python3 -m pytest tests/test_inbox_merge.py tests/test_runner_brief.py`
- 성공 기준:
  - Completed default run-all merges into target, archives state, removes the generated task worktree, and closes the source branch.
  - Cleanup never uses force by default.
  - Cleanup is held with a clear reason when dirty paths, merge failure, archive failure, or branch deletion failure occurs.
  - Cleanup can be retried without rerunning completed implementation units.
- 중단 조건:
  - The archive cannot preserve enough diagnostics to inspect what happened after worktree removal.
  - The implementation needs `git worktree remove -f` or `git branch -D` to pass normal cases.

### Commit 6: Update README and 구현커밋 skill docs

- 대상 파일:
  - `README.md`
  - `skills/구현커밋/SKILL.md`
  - `/Users/moonsoo/projects/codex-skills-user/구현커밋/SKILL.md`
  - `/Users/moonsoo/projects/codex-skills-user/구현커밋/agents/openai.yaml`
- 변경:
  - Apply the `Skill-Creator Based Skill Update Contract` above.
  - Document task-worktree-first route behavior.
  - Document that `.codex-flow/` state lives inside the task worktree for the active plan.
  - Document completed-run finalization: local merge, `.codex-flow` archive, generated task worktree removal, and `git branch -d` branch close.
  - Document held cleanup states and the escape hatch for keeping worktrees.
  - Update command examples so the plan path points to the task worktree.
  - Sync `Plan Source Auto-Discovery` from canonical installed skill into the codex-flow mirror skill.
  - Update frontmatter `description` in both SKILL.md files so task worktree and safe finalize cleanup can trigger the skill.
  - Update `agents/openai.yaml` `short_description` and `default_prompt` so UI metadata no longer implies a plan/branch-only or PR-dry-run-only workflow.
  - Update skill docs only after runtime behavior exists.
- 코드 스니펫:

```yaml
interface:
  display_name: "구현커밋"
  short_description: "worktree 기반 구현, 리뷰, 병합, 정리를 관리합니다."
  default_prompt: "$구현커밋을 사용해서 이 계획을 task worktree에서 실행하고, 완료 후 안전하게 merge/archive/cleanup까지 진행해줘."
```

The exact text may be adjusted, but it must mention `$구현커밋`, task worktree execution, and safe finalization.
- 검증:
  - `git diff --check`
  - YAML frontmatter parse for skill docs and agent metadata.
  - `rg -n "task worktree|worktree|cleanup|archive|\\.codex-flow|run-all|route" README.md skills/구현커밋/SKILL.md /Users/moonsoo/projects/codex-skills-user/구현커밋/SKILL.md`
  - `rg -n "Plan Source Auto-Discovery|cleanup_held|git worktree remove|git branch -d|keep-worktree|no-cleanup" skills/구현커밋/SKILL.md /Users/moonsoo/projects/codex-skills-user/구현커밋/SKILL.md`
  - `wc -l skills/구현커밋/SKILL.md /Users/moonsoo/projects/codex-skills-user/구현커밋/SKILL.md`
- 성공 기준:
  - Docs no longer imply route writes state into a long-lived root by default.
  - Docs clearly say completed plans archive state before removing generated worktrees.
  - Canonical and mirror skill docs carry the same user-visible workflow guarantees.
  - `agents/openai.yaml` no longer advertises only `plan/branch` and `PR dry-run`.
  - User-facing skill and runtime README say the same thing.
- 중단 조건:
  - Runtime behavior and documentation disagree.
  - Canonical and mirror skill docs disagree on auto-discovery or cleanup semantics.
  - The skill update grows enough that the core workflow becomes hard to scan; if it approaches 500 lines, split stable detail into a direct reference file and link it from SKILL.md.

## Plan Quality Check

- Alternative considered: skill-only lane discipline.
  - Rejected because runtime still writes state wherever `--repo` points.
- Alternative considered: central external state store.
  - Rejected for this phase because many callers assume the plan lives under the execution repo.
- Alternative considered: remove generated worktrees with `git worktree remove -f`.
  - Rejected as a default because it can delete untracked diagnostics or dirty user work.
- Why this plan:
  - It changes the default state birth location at route time, removes fragile repo inference incrementally, and closes generated worktrees after safe merge/archive gates.
- Tradeoff:
  - Gain: task state, branch, prompts, and logs are scoped to the task worktree.
  - Gain: completed worktrees do not accumulate after successful finalize.
  - Cost: dashboard and PR locks become task-local in phase 1, and closeout needs a durable archive path.
  - Accepted because recurrence prevention is more urgent than global aggregation.
- What this plan may still miss:
  - Multi-worktree dashboard aggregation.
  - Repo-global PR lock coordination.
  - Migration tooling for existing active `.codex-flow` plans.
- When to stop and revise:
  - If route cannot safely create/reuse worktrees without changing the source repo branch.
  - If source drift cannot reliably resolve the original source plan.
  - If legacy plans fail after resolver changes.
  - If cleanup cannot preserve `.codex-flow` diagnostics before removing the task worktree.
  - If normal cleanup requires force removal or force branch deletion.

## 구현 후 검토 리스트

- 회귀 확인:
  - Existing `route`, `run-next`, `run-all`, `open-pr`, `create-pr`, `merge`, `mark`, `dashboard`, and `morning-brief` still work for legacy plans.
  - Completed `run-all --auto-resolve` still merges and closes branches, now with archive/worktree cleanup gates.
- 검증 확인:
  - `python3 -m py_compile` on changed modules.
  - Targeted pytest only after confirming the package-supply-chain freeze allows package-backed test execution.
  - CLI smoke with temporary git repo, task worktree, integration merge, archive verification, and worktree removal.
- 리뷰 관점:
  - `review-all-in-one` should check resolver consistency, source drift correctness, dirty worktree handling, cleanup hold states, archive preservation, and docs/runtime sync.
- Operator 재확인:
  - Confirm whether task-local PR locks are acceptable for phase 1 before remote PR automation is expanded.
  - Confirm whether the default archive location is acceptable after the first implementation pass.

## 후행 실행

후행 실행: `구현커밋`

이 계획이 승인되면 `$구현커밋`은 이 plan-first 문서를 route source로 사용해야 한다. 실제 구현 패치는 이 plan-first 단계에서 시작하지 않는다.

## 테스트 링크

해당 없음. 이번 산출물은 CLI/runtime 구현 계획 문서이며 로컬 웹 UI나 배포 URL이 없다.

대체 검증:

- `git diff --check`
- `python3 -m py_compile <changed-runtime-files>`
- supply-chain freeze 확인 후 허용되는 경우에만 targeted pytest 실행
