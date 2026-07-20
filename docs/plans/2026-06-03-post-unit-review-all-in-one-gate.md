# 2026-06-03 | Post-Unit Review-All-In-One Gate | codex-flow-post-unit-review-gate

## Scope

Codex Flow already runs a review step before committing each unit. This change strengthens that existing step so every executable commit unit must pass a `review-all-in-one` style gate before Codex Flow creates the git commit.

## 검토용 결과물

- 계획 MD: this file
- 테스트 링크:
  - Localhost: 해당 없음. CLI/runtime prompt contract change.
  - Deploy: 해당 없음. Local package tests are the verification surface.
- 상태: verified
- 실제 동작:
  - `run-next` and `run-all` review prompts require `review-all-in-one` before `COMMIT_UNIT_READY`.
- Mock:
  - 없음

## HTML 생략 보고서

- 판정: 생략 가능
- 생략 사유:
  - CLI/runtime prompt contract and docs change. No screen, layout, or interaction artifact is needed.
- 대체 검토물:
  - Unit tests and generated prompt assertions.
- 테스트 링크:
  - Localhost: 해당 없음. `python3 -m pytest`로 검증.
  - Deploy: 해당 없음.
- 사용자가 바로 열어볼 링크:
  - this plan file

## Operator 결정 필요 사항

- 상태: 없음
- Codex가 선택한 기본값:
  - Existing per-unit review call을 별도 command로 늘리지 않고 `review-all-in-one` 필수 gate로 강화한다.
  - 이유: `run-next`, `run-all`, `open-pr/create-pr --auto-resolve`, `merge --auto-resolve`가 모두 같은 execute path를 쓰므로 한 지점에서 보강하는 편이 가장 좁고 일관적이다.

## Plan Quality Check

- Alternative considered: Separate `review-all-in-one` CLI phase after every commit unit. Rejected because it would duplicate the already existing review call and add another control path.
- Why this plan: Codex Flow commits only after the review result is ready, so the existing review prompt is the correct gate to strengthen.
- What this plan may still miss: The runtime can require the skill in the prompt, but the spawned Codex session must still have access to the local skill file.
- When to stop and revise: If tests show `run-next` can commit without the post-unit review prompt, stop and move the gate into runner-level validation.

## Implementation Plan

### Commit 1: Add mandatory post-unit review-all-in-one gate to review prompts

- 대상 파일:
  - `codex_flow/implementer_agent.py`
  - `codex_flow/runner.py`
- 변경:
  - Commit unit review prompt에 `review-all-in-one` 필수 적용 규칙을 추가한다.
  - Preview/generated prompt에도 같은 post-unit review gate를 보여준다.
- 검증:
  - `python3 -m pytest tests/test_agent_roles.py tests/test_runner_brief.py`
- 성공 기준:
  - Build review prompt includes `review-all-in-one`.
  - Preview prompt tells the executing agent that commit happens only after the gate.
- 중단 조건:
  - Existing parse contract `COMMIT_UNIT_READY` / `COMMIT_UNIT_NEEDS_WORK`가 깨지면 prompt wording만 조정한다.

### Commit 2: Document the between-commit review gate

- 대상 파일:
  - `README.md`
  - `skills/codex-flow/SKILL.md`
  - `skills/코덱스플로우/SKILL.md`
  - `/Users/moonsoo/projects/codex-skills-user/codex-flow/SKILL.md`
  - `/Users/moonsoo/projects/codex-skills-user/코덱스플로우/SKILL.md`
- 변경:
  - `run-next` / `run-all`이 unit별 자동 커밋 전에 `review-all-in-one` gate를 통과한다는 계약을 문서화한다.
- 검증:
  - `rg -n "review-all-in-one|post-unit|커밋 전" ...`
- 성공 기준:
  - Runtime README와 installed skill alias가 같은 설명을 갖는다.
- 중단 조건:
  - 문서가 Final Gate와 per-unit gate를 혼동하게 만들면 표현을 나눠 쓴다.

### Commit 3: Run focused and full tests

- 대상 파일:
  - `tests/test_agent_roles.py`
  - `tests/test_runner_brief.py`
- 변경:
  - Prompt contract tests를 보강한다.
- 검증:
  - `python3 -m pytest tests/test_agent_roles.py tests/test_runner_brief.py`
  - `python3 -m pytest`
- 성공 기준:
  - Focused tests pass.
  - Full package tests pass or any unrelated pre-existing failure is clearly reported.
- 중단 조건:
  - Full test failure affects changed review/commit behavior.

## Verification Results

- Focused tests:
  - `python3 -m pytest tests/test_agent_roles.py tests/test_runner_brief.py`
  - Result: `25 passed in 13.72s`
- Full tests:
  - `python3 -m pytest`
  - Result: `79 passed in 17.32s`
