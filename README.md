# Codex Flow

Codex Flow is a small, local-first orchestration layer for running Codex work as ticketed commit units.

It turns a broad request into:

```text
ticket -> plan queue -> run-next/run-all -> commit unit -> review artifact -> PR draft/merge gate
```

The project is intentionally conservative. It keeps work in small units, records local state in `.codex-flow/`, and gives AI agents explicit recovery paths for common automation blockers.

## What It Does

- Creates a local `.codex-flow/` workspace for tickets, plans, queues, briefs, and locks.
- Routes a natural-language request into an inbox, an existing plan, or a new plan queue.
- Generates implementer prompts for the next ready unit.
- Can execute Codex CLI for one unit or a sequence of units.
- Can commit changed files per completed unit.
- Writes morning briefs, review checklists, and PR dry-run artifacts.
- Provides auto-resolve behavior for dirty worktrees, active PR locks, unfinished units, and local merge readiness.
- Provides a detailed dashboard with PR lock, inbox, dirty file, active plan, progress, and suggested command summaries.
- Supports PR lock management, PR status checks, and inbox drain after merged PRs.

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

Route a request:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo route "Improve the onboarding flow" --auto-resolve
```

Route directly to an existing plan:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo route \
  "Polish the copy in the same onboarding plan" \
  --plan /path/to/your/repo/.codex-flow/plans/<slug>/plan.md
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
  --auto-resolve --execute --commit
```

Run all ready units:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo run-all \
  --plan /path/to/your/repo/.codex-flow/plans/<slug>/plan.md \
  --auto-resolve --execute --commit --max-units 4
```

Run all ready units and prepare a PR draft artifact:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo run-all \
  --plan /path/to/your/repo/.codex-flow/plans/<slug>/plan.md \
  --auto-resolve --execute --commit --open-pr
```

Run all ready units and merge locally after completion:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo run-all \
  --plan /path/to/your/repo/.codex-flow/plans/<slug>/plan.md \
  --auto-resolve --execute --commit --merge --target main
```

Create a PR draft artifact:

```bash
python3 scripts/codex_flow.py --repo /path/to/your/repo open-pr \
  --plan /path/to/your/repo/.codex-flow/plans/<slug>/plan.md \
  --auto-resolve --execute-units --commit --dry-run
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
$코덱스플로우 병합
```

Core meanings:

- `라우트`: create a ticket and plan queue from the request.
- `다음실행`: run the next ready unit with `run-next`.
- `모두실행`: run ready units with `run-all`.

## Auto-Resolve Policy

| Blocker | Auto-resolve behavior |
| --- | --- |
| Dirty worktree | Stashes non-`.codex-flow/` changes before execution. |
| Active PR lock | Creates a stacked plan and logs the lock resolution. |
| Unfinished units before PR draft | Runs unfinished units before writing the PR artifact. |
| Local merge readiness | Runs unfinished units before local merge. |

Remote PR creation and remote merge are intentionally separate commands. Use them only when you are ready to make externally visible GitHub changes.

## Crack-CLI-Inspired Features

Codex Flow intentionally borrows the strongest operational ideas from Crack-CLI while staying Python-first and skill-friendly:

- active-plan routing before creating unnecessary new branches
- explicit PR lock files
- inbox drain after a merged PR
- dashboard summaries with suggested next commands
- `run-next` and `run-all` commit-unit execution
- optional `run-all --open-pr` and `run-all --merge` finalize paths
- local-first default behavior with remote operations kept explicit

Codex Flow differs by keeping a machine-readable `queue.json` next to readable Markdown, shipping Korean Codex skill aliases, and using `--auto-resolve` to preserve dirty worktree changes with `git stash` instead of simply stopping.

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
python3 scripts/codex_flow.py route "request"
python3 scripts/codex_flow.py route "request" --plan .codex-flow/plans/<slug>/plan.md
python3 scripts/codex_flow.py dashboard
python3 scripts/codex_flow.py dashboard --watch
python3 scripts/codex_flow.py run-next --plan .codex-flow/plans/<slug>/plan.md --auto-resolve --execute --commit
python3 scripts/codex_flow.py run-all --plan .codex-flow/plans/<slug>/plan.md --auto-resolve --execute --commit
python3 scripts/codex_flow.py run-all --plan .codex-flow/plans/<slug>/plan.md --auto-resolve --execute --commit --open-pr
python3 scripts/codex_flow.py run-all --plan .codex-flow/plans/<slug>/plan.md --auto-resolve --execute --commit --merge
python3 scripts/codex_flow.py set-pr-lock --branch codex/demo --pr-url https://github.com/example/repo/pull/1
python3 scripts/codex_flow.py pr-check
python3 scripts/codex_flow.py drain
python3 scripts/codex_flow.py clear-pr-lock
```

## Safety Notes

Codex Flow does not use `git reset --hard` to clean user work. Auto-resolve preserves dirty files with `git stash`.

The `.codex-flow/` directory may contain local planning context. Review it before publishing project-specific work.
