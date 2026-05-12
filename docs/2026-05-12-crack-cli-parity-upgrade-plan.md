# Crack-CLI Parity Upgrade Plan

## Objective

Upgrade Codex Flow from a small public seed into a more Crack-CLI-comparable orchestrator while keeping the implementation Pythonic, local-first, and safe for public use.

## Crack-CLI Advantages To Borrow

- Route requests to an existing plan, a new plan, or an inbox depending on current state.
- Keep branch and plan state readable.
- Provide a dashboard with PR lock, inbox, dirty file, plan progress, recent log, and suggested next command.
- Support PR lock management, `pr-check`, and `drain`.
- Let `run-all` finish a plan and then open a PR draft or merge when requested.
- Keep remote operations explicit.

## Implementation Scope

- `codex_flow/cli.py`
  - Add route options: `--plan`, `--branch`, `--title`, `--reason`.
  - Add dashboard watch options.
  - Add run-all finalize flags: `--merge`, `--target`, `--remote`, `--open-pr`.
  - Add `set-pr-lock`, `clear-pr-lock`.
  - Add `pr-check --gh-command`.
- `codex_flow/plans.py`
  - Add active plan discovery and plan-local request queue support.
  - Allow dynamic plan title and branch.
- `codex_flow/dashboard.py`
  - New detailed dashboard renderer.
- `codex_flow/pr.py`
  - Add lock parsing, lock clearing, GitHub status check, and drain after merged PR.
- `README.md`, skill docs, tests.

## 검토용 결과물

## HTML 생략 보고서

- 판정: 생략 가능
- 생략 사유:
  - 이번 작업은 CLI behavior, state files, GitHub PR lock handling, tests, README update가 핵심이다.
  - 화면/디자인/인터랙션 검토 대상이 아니므로 HTML artifact는 만들지 않는다.
- 대체 검토물:
  - `python3 -m pytest`
  - `python3 scripts/codex_flow.py --help`
  - updated README command docs
- 사용자가 바로 열어볼 링크:
  - `README.md`

## Verification

```bash
python3 -m pytest
python3 scripts/codex_flow.py --help
rg -n "<private pattern set>" . -g '!**/.git/**'
```
