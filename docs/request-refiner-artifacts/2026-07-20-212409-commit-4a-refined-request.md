# Refined Request

## Original Request

Commit 4A를 진행한다. 기존 `research.md`와 보강된 plan을 재사용하되, 요청개선 → research → plan-first 보강 → 구현 전 `review-all-in-one` → `해결전략검토` → `구현커밋` 순서로 실행하고, 구현 완료 뒤 `review-all-in-one`과 `테스트`로 최종 검증한다. 메인 에이전트가 기본 실행자이며 구현 단위는 순차 실행한다.

## Refined Execution Request

현재 `codex/implementation-commit-adaptive-execution-runtime` 작업 브랜치의 확정된 Commit 1~3 및 Commit 4 부분 구현을 보존한 상태에서, 승인된 Commit 4A 범위인 reviewer contract separation을 원자적으로 구현한다.

구현 목표는 writable implementer와 read-only reviewer를 별도 인터페이스·별도 child session으로 분리하고, reviewer가 `sandbox=read-only`, session resume 금지, repair 금지 계약을 지키도록 만드는 것이다. reviewer 실행 전후 HEAD 및 tracked/untracked worktree digest가 동일하지 않거나, reviewer가 malformed internal gate를 반환하거나, resume/repair 경로가 호출되면 fail-closed 해야 한다.

Commit 4B의 profile dispatcher, docs lease, verification registry, Commit 4C의 final-gate producer, Commit 4D의 finalize guard·activation state는 이번 구현 범위에 포함하지 않는다. 기존 사용자 변경과 실패 증거를 삭제·rollback하지 않으며, 검증된 4A 변경만 별도 커밋으로 고정한다.

## Working Brief

- Intent: Commit 4A reviewer isolation 계약을 실제 runtime과 테스트에 구현한다.
- Work type: 코드 리팩터링, 회귀 테스트 보강, 독립 검토, 검증, 커밋·푸시 패키징.
- Target: `/Users/moonsoo/projects/.codex-flow-codex-flow-worktrees/implementation-commit-adaptive-execution-plan`의 `codex_flow/implementer_agent.py`, `codex_flow/reviewer_agent.py`, `codex_flow/runner.py`, 필요한 좁은 helper와 `tests/test_final_gate.py`, `tests/test_agent_roles.py`, 직접 관련 회귀 테스트.
- Constraints: 4A 밖의 기능 확장 금지; reviewer write/resume/repair 금지; 기존 partial commits와 evidence 보존; destructive cleanup·force git 금지; 구현 단계 병렬 run-all 금지.
- Success check: focused tests와 직접 관련 회귀 suite 통과, reviewer non-mutation/HEAD·digest invariant/malformed gate/resume·repair rejection 증명, 독립 read-only diff review에서 blocker·important 0, `git diff --check` 통과, 별도 4A 커밋 생성 및 upstream이 안전하게 확인되면 push.
- Routing: `research` → `plan-first-implementation` → pre-implementation `review-all-in-one` + `해결전략검토` → `구현커밋` → post-implementation `review-all-in-one` + `테스트`.

## Execution Notes

- Execution mode: refine-then-execute
- Artifact role: source prompt for Commit 4A research, plan, implementation, and verification
- Orchestration mode: `fixed-workflow`
- Main executor: root agent
- Delegation: implementation stays local; one existing evaluator may be reused for independent read-only diff review after the main implementation passes focused tests.
- Integration order: planning artifacts → Commit 4A code/tests → independent review → repair if in-scope → final verification → commit/push decision.
- Stop conditions: implementation-before blocker/replan finding, reviewer mutation not provably blocked, required verification failure that cannot be repaired inside 4A, unsafe git state, or scope expansion into Commit 4B+.

## Execution Result

Pending. This section will be updated after Commit 4A implementation, review, verification, and packaging finish or reach a documented blocker.
