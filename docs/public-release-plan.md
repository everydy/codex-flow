# Public Release Plan

## Goal

Publish Codex Flow as a public-safe repository with only reusable CLI, tests, README, and skill documentation.

## Scope

- Include:
  - `codex_flow/`
  - `scripts/codex_flow.py`
  - `skills/codex-flow/SKILL.md`
  - `skills/코덱스플로우/SKILL.md`
  - `tests/`
  - `README.md`
  - `pyproject.toml`
  - `LICENSE`
- Exclude:
  - private memory files
  - local `.codex-flow/` state
  - `__pycache__`
  - user-specific absolute paths
  - unrelated skill repository history

## 검토용 결과물

## HTML 생략 보고서

- 판정: 생략 가능
- 생략 사유:
  - 이번 작업은 공개용 CLI repo 구성, README 작성, 개인정보 제거, 테스트/스캔 검증이다.
  - 화면, 상태 인터랙션, 디자인 검토가 핵심이 아니므로 HTML artifact가 필요하지 않다.
- 대체 검토물:
  - `README.md`
  - `python3 -m pytest`
  - privacy scan command
- 사용자가 바로 열어볼 링크:
  - local README: `README.md`

## Verification

```bash
python3 -m pytest
rg -n "<private pattern set>" .
git status --short
```
