# Codex Flow

Codex Flow is a small, local-first orchestration layer for running Codex work as ticketed commit units.

It turns an approved plan-first Markdown document into:

```text
plan-first source -> source snapshot -> tickets -> macro plan -> queue -> run-next/run-all -> post-unit review-all-in-one gate -> commit -> PR draft/merge gate
```

The project is intentionally conservative. It keeps work in small units, records local state in `.codex-flow/`, and gives AI agents explicit recovery paths for common automation blockers.
Each plan also carries a `Skill Routing Manifest`, so a fresh Codex session can see which existing skills should guide each commit unit and final review gate.

## What It Does

- Creates a local `.codex-flow/` workspace for tickets, plans, queues, briefs, and locks.
- Routes a plan-first Markdown source into a source snapshot, extracted tickets, a macro plan, and a queue.
- Rejects short natural-language route input instead of creating an ad hoc plan.
- Writes a `Skill Routing Manifest` into every plan, then feeds the selected entry into implementer prompts, review checklists, and PR draft artifacts.
- Executes commit units by default, while still supporting prompt previews with `--preview` or `--dry-run`.
- Can execute Codex CLI for one unit or a sequence of units with an implement-then-review agent loop.
- Can commit changed files per completed unit after a mandatory post-unit `review-all-in-one` gate.
- Writes morning briefs, review checklists, and PR dry-run artifacts.
- Provides auto-resolve behavior for dirty worktrees, active PR locks, unfinished units, and local merge readiness.
- Provides a detailed dashboard with PR lock, inbox, dirty file, active plan, progress, and suggested command summaries.
- Supports PR lock management, PR status checks, and source-plan-only inbox drain after merged PRs.

## Install

Clone the repository and run the script directly:

```bash
git clone https://github.com/everydy/codex-flow.git
cd codex-flow
python3 scripts/codex_flow.py --help
```

For development:

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest
```

## Quick Start

Initialize Codex Flow in a target repository:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo init
python3 scripts/codex_flow.py --repo /path/to/your/repo status
```

Route an approved plan-first document:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo route docs/plans/example-plan.md --auto-resolve
```

Inspect the dashboard:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo dashboard
python3 scripts/codex_flow.py --repo /path/to/your/repo dashboard --watch --interval 5
```

Run the next unit:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo run-next \
  --plan /path/to/your/repo/.codex-flow/plans/<slug>/plan.md \
  --auto-resolve
```

Each executed unit is implemented, reviewed in the same Codex session with a mandatory `review-all-in-one` post-unit gate, and only then committed. If that review finds blocker or important issues, the unit returns `needs_work` instead of being committed.

Run all executable commit units until the plan is complete or a unit needs work:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo run-all \
  --plan /path/to/your/repo/.codex-flow/plans/<slug>/plan.md \
  --auto-resolve
```

If a source plan cannot be split into `### Commit N:` or `### Phase N:` sections, Codex Flow creates a low-confidence placeholder unit and holds it at `human_gate` instead of auto-running it. Split the source plan into clear phases, then route it again.

Run all executable commit units and prepare a PR draft artifact:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo run-all \
  --plan /path/to/your/repo/.codex-flow/plans/<slug>/plan.md \
  --auto-resolve --open-pr
```

Run all executable commit units and merge locally after completion:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo run-all \
  --plan /path/to/your/repo/.codex-flow/plans/<slug>/plan.md \
  --auto-resolve --merge --target main
```

Create a PR draft artifact:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo open-pr \
  --plan /path/to/your/repo/.codex-flow/plans/<slug>/plan.md \
  --auto-resolve --dry-run
```

Create a real remote draft PR after readiness:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo create-pr \
  --plan /path/to/your/repo/.codex-flow/plans/<slug>/plan.md \
  --auto-resolve
```

Preview prompts without running Codex:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo run-next \
  --plan /path/to/your/repo/.codex-flow/plans/<slug>/plan.md \
  --preview
python3 scripts/codex_flow.py --repo /path/to/your/repo run-all \
  --plan /path/to/your/repo/.codex-flow/plans/<slug>/plan.md \
  --preview
```

## Korean Shortcut Skill

This repository includes a Korean Codex skill alias in `skills/코덱스플로우/SKILL.md`.

Common commands:

```text
$코덱스플로우
$코덱스플로우 상태
$코덱스플로우 라우트 <요청>
$코덱스플로우 다음실행
$코덱스플로우 모두실행
$코덱스플로우 브리핑
$코덱스플로우 리뷰
$코덱스플로우 PR초안
$코덱스플로우 PR생성
$코덱스플로우 병합
```

Core meanings:

- `라우트`: create a ticket and plan queue from the request.
- `다음실행`: run the next incomplete commit unit with `run-next`.
- `모두실행`: run commit units with `run-all` until complete or needs_work.

## Skill Routing Manifest

Codex Flow does not replace specialist skills such as `요청개선`, `mission-completion-harness`, `review-all-in-one`, or `qa-gate`. Instead, `plan.md` contains a routing table:

```md
## Skill Routing Manifest

| Phase | Required skills | Optional skills | Evidence |
| --- | --- | --- | --- |
| Commit 1: Scope lock | `요청개선`, `plan-first-implementation` | `community-research` | Requirements and the implementation gate need locking. |
| Commit 2: Implementation | `plan-first-implementation`, `mission-completion-harness` | `디자인올인원` | A focused code unit must be completed. |
| Final Gate | `review-all-in-one`, `qa-gate` | `checkpoint` | Review and verification decide readiness. |
```

`run-next` and `run-all` read the selected manifest entry and include it in the implementer prompt. `review` and `open-pr --dry-run` include the same manifest so the daytime review can check whether the right skills were used or explicitly skipped with a fallback reason.
Separately from the final gate, every executed commit unit has a mandatory post-unit `review-all-in-one` gate before Codex Flow creates the git commit. This between-commit gate is part of the execution loop, so it applies to `run-next`, `run-all`, and auto-resolve finalization paths that execute unfinished units.
Codex Flow automatically repairs implementation-like manifest rows so `plan-first-implementation` is required for feature, UI/design/layout, refactor, integration, API/DB/routing, or other code implementation units. It leaves status, review, briefing, QA-only, and test-only rows alone unless they also need an implementation plan gate.

## Auto-Resolve Policy

| Blocker | Auto-resolve behavior |
| --- | --- |
| Dirty worktree | Stashes non-`.codex-flow/` changes before execution. |
| Active PR lock | Keeps the lock meaningful and queues new requests in inbox. |
| Unfinished units before PR draft | Runs unfinished units before writing the PR artifact. |
| Local merge readiness | Runs unfinished units before local merge. |
| Transient `needs_work` review | Retries the same unit with bounded repair context when `--auto-resolve` is active; if the repair budget is exhausted, records partial changed paths so the next repair run can keep them as input. |

Remote PR creation and remote merge are normal Codex Flow finalization steps when the current workflow calls for GitHub integration. They remain separate CLI modes so automation can choose them deliberately and log the result.

## Crack-CLI-Inspired Features

Codex Flow intentionally borrows the strongest operational ideas from Crack-CLI while staying Python-first and skill-friendly:

- role-separated Router, Planner, Implementer, and Merge agent modules
- Markdown `plan.md` plus `log.md` as the primary execution progress source, with `queue.json` kept as a compatibility cache
- plan-level `Skill Routing Manifest` that routes existing Codex skills per commit unit instead of hiding that decision in chat context
- active-plan routing before creating unnecessary new branches
- explicit PR lock files
- PR lock clearing after merged PRs, plus source-plan-only inbox drain after review locks are cleared
- dashboard summaries with suggested next commands
- `run-next` and `run-all` commit-unit execution
- optional `run-all --open-pr` and `run-all --merge` finalize paths
- local-first default behavior with remote operations kept in finalize commands

Codex Flow differs by keeping explicit `--preview` and `--dry-run` escape hatches, shipping Korean Codex skill aliases, and using `--auto-resolve` to preserve dirty worktree changes with `git stash` instead of deleting or reverting them.

## Agent Architecture

Codex Flow now separates orchestration roles instead of treating every Codex call as one generic execution:

```text
Source route      adopts plan-first Markdown into source snapshots and queue units
Planner helpers   maintain commit-sized plan.md units and Skill Routing Manifest rows
Implementer agent implements one unit, then resumes the same session for review
Merge agent       resolves only active merge conflicts
```

The default route path now adopts a plan-first Markdown source. Short natural-language route input fails with guidance instead of creating a generic plan. `run-next` and `run-all` execute by default and commit completed units unless `--no-commit` is explicit. Use `--preview` or `--dry-run` when you only want prompts. If the original plan-first source changes after route, `run-next` and `run-all` stop unless `--accept-source-drift` is explicit.

Plan progress is read from readable Markdown:

```text
.codex-flow/plans/<slug>/
  plan.md       canonical commit units, with headings like ### Commit 1: ...
                and Skill Routing Manifest entries for each unit
  source-plan.md snapshot of the original plan-first source adopted by route
  source.json   source metadata, including original path and hash
  macro-plan.md extracted ticket order, dependencies, and stop conditions
  log.md        canonical completion records, including Completed commit unit N.
  queue.json    machine-readable cache for compatibility
  queue.md      rendered cache
  requests.md   plan-local follow-up requests
```

## Repository Layout

```text
codex_flow/              Python package
scripts/codex_flow.py    CLI entrypoint
skills/                  Codex skill docs
tests/                   pytest test suite
```

## Command Reference

```bash
python3 scripts/codex_flow.py init
python3 scripts/codex_flow.py route docs/plans/example-plan.md --auto-resolve
python3 scripts/codex_flow.py dashboard
python3 scripts/codex_flow.py dashboard --watch
python3 scripts/codex_flow.py run-next --plan .codex-flow/plans/<slug>/plan.md --auto-resolve
python3 scripts/codex_flow.py run-next --plan .codex-flow/plans/<slug>/plan.md --accept-source-drift
python3 scripts/codex_flow.py run-next --plan .codex-flow/plans/<slug>/plan.md --preview
python3 scripts/codex_flow.py run-all --plan .codex-flow/plans/<slug>/plan.md --auto-resolve
python3 scripts/codex_flow.py run-all --plan .codex-flow/plans/<slug>/plan.md --accept-source-drift
python3 scripts/codex_flow.py run-all --plan .codex-flow/plans/<slug>/plan.md --preview
python3 scripts/codex_flow.py run-all --plan .codex-flow/plans/<slug>/plan.md --auto-resolve --open-pr
python3 scripts/codex_flow.py create-pr --plan .codex-flow/plans/<slug>/plan.md --auto-resolve
python3 scripts/codex_flow.py run-all --plan .codex-flow/plans/<slug>/plan.md --auto-resolve --merge
python3 scripts/codex_flow.py set-pr-lock --branch codex/demo --pr-url https://github.com/example/repo/pull/1
python3 scripts/codex_flow.py pr-check
python3 scripts/codex_flow.py drain
python3 scripts/codex_flow.py clear-pr-lock
```

## Safety Notes

Codex Flow does not use `git reset --hard` to clean user work. Auto-resolve preserves dirty files with `git stash`.

The `.codex-flow/` directory may contain local planning context. Review it before publishing project-specific work.
