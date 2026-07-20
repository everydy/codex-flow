# Refined Request

## Original Request

현재 Commit 4A 구현을 더 이상 세부 커밋으로 분리하거나 장시간 반복 검토하지 말고, 기존 dirty diff를 전부 점검해 불필요한 복잡성과 중복을 제거한 뒤 가장 단순한 안전 구현으로 빠르게 마무리한다. 구현 전에 요청개선, research, plan-first, review-all-in-one, 해결전략검토를 적용하고, 승인된 단일 구현 단위를 구현커밋으로 실행한 뒤 최종 review-all-in-one과 테스트를 수행한다.

## Refined Execution Request

`a722017` 이후의 미커밋 Commit 4A 후보를 보존한 상태에서, writable implementer와 fresh read-only reviewer 분리라는 핵심 목표만 남기고 과도한 예외·중복·추상화를 제거한다. 작업을 추가 하위 커밋으로 나누지 않고 단일 Commit 4A 코드 커밋으로 완료한다.

구현 전에 확인된 Important 두 건을 우선 해결한다. `.codex-flow/` 전체를 mutation digest에서 제외하지 않고 reviewer 실행 중 runner가 정상적으로 변경하는 정확한 control-plane 파일만 제외한다. `INTERNAL_REVIEW_GATE`는 ready와 needs_work 모두에서 정확히 한 줄과 필수 필드를 요구하며, malformed gate·session reuse·mutation은 non-retryable protocol failure로 분류한다. reviewer process failure는 `phase=review`, invariant 재검사, zero-commit, held evidence를 유지한다.

runner의 반복 failure bookkeeping은 동작을 바꾸지 않는 작은 공용 helper로 합칠 수 있을 때만 축소한다. Commit 4B 이상, 새로운 event system, 외부 dependency, UI, 배포, PR/merge는 범위 밖이다.

## Working Brief

- Intent: 현재 4A dirty diff를 재사용해 가장 작은 안전 구현으로 즉시 마무리한다.
- Target: `codex_flow/git_ops.py`, `implementer_agent.py`, `reviewer_agent.py`, `runner.py`와 직접 관련된 5개 테스트 파일.
- Preserve: fresh reviewer session, read-only sandbox, no resume/no repair, exact phase attribution, held evidence, existing repair/commit behavior.
- Remove/simplify: `.codex-flow/` blanket exclusion, incomplete gate validation, wrong failure taxonomy, raw session id artifact if not required, avoidable duplicate plumbing, EOF whitespace.
- Success: one implementation commit, focused tests and full pytest pass, residual search/diff-check pass, final independent review blocker=0/important=0.
- Stop: scope expansion into 4B+, test weakening, reviewer mutation escape, destructive cleanup, or unrelated worktree changes.

## Execution Notes

- Mode: `fixed-workflow`; main agent is the sole implementer.
- Delegation: reuse one existing read-only evaluator only for the pre/post independent review; no implementation delegation.
- Research: update the existing `research.md` once with the empirical dirty-diff findings and smallest accepted option.
- Plan: update the existing single-unit `commit-4a-plan.md`; do not create 4A-1/4A-2 units.
- Implementation order: contract simplification → focused tests → all related tests → full suite → independent final review → one code commit → push if upstream is safe.
- Review budget: no review after every edit; only the required pre-implementation gate and one final gate, with repairs driven by concrete failing tests/findings.

## Execution Result

Completed as one Commit 4A implementation package.

- Implementer and reviewer are separate runtime roles; the reviewer always starts a fresh read-only session and never owns repair.
- Candidate mutation detection excludes only the active parent-owned review diagnostics and ledger files, not `.codex-flow/` as a whole.
- Missing, malformed, duplicate, reused-session, mutation, and process-failure review evidence fails closed with explicit non-retryable protocol/process taxonomy.
- Structured review artifacts retain hashes and invariant booleans without raw session identifiers.
- Focused invalid-gate regression tests passed (`3 passed`).
- Final full suite passed (`230 passed`), `git diff --check` and Python compilation passed.
- Independent final review passed with `Blocker 0 / Important 0`.
- The verified files are packaged together in the single implementation commit created from this request; no 4B+ work is included.
