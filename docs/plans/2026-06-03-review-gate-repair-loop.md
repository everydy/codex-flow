# 2026-06-03 | Review Gate Repair Loop | codex-flow-review-gate-repair-loop

## Scope

Codex Flow already asks the post-unit review pass to apply `review-all-in-one`. This plan turns that prompt-level gate into a structured execution gate. The runtime will parse a `REVIEW_GATE` line when present, score blockers/important/minor findings, and use that score to decide commit, repair, or `needs_work`.

## 검토용 결과물

- 계획 MD: this file
- 테스트 링크:
  - Localhost: 해당 없음. CLI/runtime behavior change.
  - Deploy: 해당 없음. Local package tests are the verification surface.
- 상태: verified
- 실제 동작:
  - Commit units can return structured review gate data.
  - Blocker/important findings keep the unit in repair instead of committing.
- Mock:
  - 없음

## HTML 생략 보고서

- 판정: 생략 가능
- 생략 사유:
  - CLI/runtime parser and execution loop change. No browser surface is involved.
- 대체 검토물:
  - Unit tests, fake Codex runner tests, and full pytest.
- 테스트 링크:
  - Localhost: 해당 없음. `python3 -m pytest`로 검증.
  - Deploy: 해당 없음.
- 사용자가 바로 열어볼 링크:
  - this plan file

## Operator 결정 필요 사항

- 상태: 없음
- Codex가 선택한 기본값:
  - `REVIEW_GATE status="pass|needs_work" blockers=N important=N minor=N reason="..."`를 새 구조화 출력으로 추가한다.
  - 기존 `COMMIT_UNIT_READY` / `COMMIT_UNIT_NEEDS_WORK`는 호환용으로 유지한다.
  - `blockers == 0 and important == 0`만 commit 가능 상태로 본다.

## Plan Quality Check

- Alternative considered: Replacing the existing `COMMIT_UNIT_*` protocol entirely. Rejected because fake runner tests and existing Codex sessions already depend on it.
- Why this plan: Adding a structured review gate gives Codex Flow a machine-readable decision without breaking existing review output.
- What this plan may still miss: The score cannot prove the spawned Codex session truly followed every sub-skill, but it can block commit when findings are reported.
- When to stop and revise: If the structured gate makes legacy fake Codex tests fail without improving safety, keep legacy parsing as the fallback.

## Implementation Plan

### Commit 1: Add structured review gate parser and score

- 대상 파일:
  - `codex_flow/implementer_agent.py`
  - `tests/test_agent_roles.py`
- 변경:
  - `ReviewGate` dataclass를 추가한다.
  - `REVIEW_GATE` line을 parse한다.
  - blocker/important/minor counts로 score와 pass 여부를 계산한다.
  - `CommitUnitReview`에 gate 데이터를 포함한다.
- 검증:
  - `python3 -m pytest tests/test_agent_roles.py`
- 성공 기준:
  - `REVIEW_GATE status="pass" blockers=0 important=0 minor=1` is parsed as ready.
  - blockers or important findings force `needs_work`.
- 중단 조건:
  - Existing `COMMIT_UNIT_READY` compatibility breaks.

### Commit 2: Wire review gate into repair loop state and logs

- 대상 파일:
  - `codex_flow/runner.py`
  - `tests/test_runner_brief.py`
- 변경:
  - review gate score/counts를 queue unit과 result에 저장한다.
  - repair attempts log에 gate score and counts를 남긴다.
  - pass only when the structured gate passes or legacy ready fallback is used.
- 검증:
  - `python3 -m pytest tests/test_runner_brief.py`
- 성공 기준:
  - fake Codex can fail once with `REVIEW_GATE needs_work` then repair and commit.
  - queue stores `review_gate` data for passed and failed units.
- 중단 조건:
  - Existing repair budget behavior changes unexpectedly.

### Commit 3: Update runtime docs and installed skills

- 대상 파일:
  - `README.md`
  - `skills/codex-flow/SKILL.md`
  - `skills/코덱스플로우/SKILL.md`
  - `/Users/moonsoo/projects/codex-skills-user/codex-flow/SKILL.md`
  - `/Users/moonsoo/projects/codex-skills-user/코덱스플로우/SKILL.md`
- 변경:
  - review gate score contract를 문서화한다.
  - `blocker=0`, `important=0`이 commit condition임을 명시한다.
- 검증:
  - `rg -n "REVIEW_GATE|blocker|important" ...`
- 성공 기준:
  - Runtime docs and user-facing skills describe the same gate.
- 중단 조건:
  - Docs imply `karpathy-loop` runs for every change without a structured gate.

### Commit 4: Run full verification

- 대상 파일:
  - `tests/test_agent_roles.py`
  - `tests/test_runner_brief.py`
- 변경:
  - Focused tests cover the new parser and runner behavior.
- 검증:
  - `python3 -m pytest tests/test_agent_roles.py tests/test_runner_brief.py`
  - `python3 -m pytest`
- 성공 기준:
  - Focused and full tests pass.
- 중단 조건:
  - Any failure in the execution/commit loop remains unresolved.

## Verification Results

- Parser focused test:
  - `python3 -m pytest tests/test_agent_roles.py`
  - Result: `5 passed in 0.03s`
- Runner gate focused tests:
  - `python3 -m pytest tests/test_runner_brief.py::test_review_gate_blocks_commit_until_important_findings_are_repaired tests/test_runner_brief.py::test_run_all_auto_resolve_repairs_transient_needs_work`
  - Result: `2 passed in 2.24s`
- Focused package tests:
  - `python3 -m pytest tests/test_agent_roles.py tests/test_runner_brief.py`
  - Result: `27 passed in 13.49s`
- Full package tests:
  - `python3 -m pytest`
  - Result: `81 passed in 17.67s`
- Diff checks:
  - `git diff --check`
  - `git diff --check -- codex-flow/SKILL.md 코덱스플로우/SKILL.md`
  - Result: passed
