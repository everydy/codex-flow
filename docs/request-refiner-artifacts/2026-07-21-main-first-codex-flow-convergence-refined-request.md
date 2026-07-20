# Refined Request

## Original Request

현재까지 한 번에 들어온 요구사항을 종합해 우선순위를 구분한 `plan-first-implementation` 문서를 만들고 보고한다. 선행 제안의 권장 순서는 Lean 4B·4C·4D 축소, `final_only` 기본값과 명시적 `per_unit`, 최종 누적 리뷰와 최소 병합 게이트, 원격 `main`과 로컬 누적 변경의 정본 수렴, 깨끗한 통합 브랜치의 PR·병합, 설치된 `구현커밋` 스킬 문서 동기화다.

## Refined Execution Request

검증이 끝난 Commit 4A 런타임을 보존한 상태에서, 기존 `implementation-commit-adaptive-execution` 계획을 main-first·orchestrator-gated 아키텍처로 재구성한다. 계획은 구현 우선순위를 P0~P4로 분리하고, 남은 Commit/Phase의 유지·축소·대체·순서 변경 여부를 명시한다.

핵심 실행 계약은 다음과 같다.

1. 일반 Commit/Phase의 기본 실행자는 메인 에이전트다.
2. Codex Flow는 plan/queue/source drift/worktree/exact-path commit/evidence/finalization을 담당하는 deterministic transaction layer로 유지한다.
3. Commit 4A에서 검증된 isolated implementer/reviewer 분리는 삭제하지 않고, high-risk·unattended·fresh-context 검증에만 쓰는 optional `isolated-child` adapter로 재배치한다.
4. 기본 review policy는 `final_only`다. `per_unit`은 계획 metadata나 현재 사용자 요청이 명시하거나 high-risk 분류가 확정된 경우에만 활성화한다.
5. 자동 repair는 제거하고, main recovery owner가 failure class와 허용 경로를 확인한 뒤 repair·rescope·local takeover·stop을 결정한다.
6. 최종 evidence 문서는 구현 unit과 분리하고 canonical tests와 필요한 Browser 검증 뒤 작성한다.
7. 원격 `main`, 기존 누적 로컬 변경, canonical skill의 최신 release handoff 계약을 의미적으로 수렴한 깨끗한 통합 브랜치에서만 PR·병합한다.
8. Codex Flow mirror를 canonical `codex-skills-user/구현커밋/SKILL.md`에 통째로 복사하지 않는다. 병합된 runtime 계약과 canonical release handoff 계약을 semantic merge한 뒤 별도 검증·동기화한다.

이번 단계의 산출물은 구현 코드가 아니라, 다른 작업자가 추가 질문 없이 순차 실행할 수 있는 우선순위 기반 plan-first 문서다. 새 PR·병합·skill sync는 계획 승인 이후의 후행 실행으로 남긴다.

## Working Brief

- Intent: 장시간 per-unit child/review 구조를 main-first 실행으로 축소하면서 4A의 격리 안전성과 canonical 배포 계약은 보존한다.
- Work type: 기존 실행 계획 재구성 및 우선순위 확정.
- Target: `docs/plans/implementation-commit-adaptive-execution/plan.md`와 직접 연결된 research/review/strategy evidence.
- Constraints: 4A rollback 금지, subagent/child 기본 실행 금지, 자동 repair 금지, canonical skill 전체 덮어쓰기 금지, 이번 단계에서 PR·merge·deploy 금지.
- Success check: 우선순위, 책임 분리, 최종 상태기계, delegation gate, child 사용 조건, acceptance metrics, Commit/Phase disposition, 정본 수렴 및 PR 순서가 한 문서에서 모순 없이 확인된다.
- Routing: `요청개선` → 기존 research 재사용 → `plan-first-implementation` 계획 보강 → 후속 구현 전 `review-all-in-one`·`해결전략검토`.

## Execution Notes

- Execution mode: refine-then-plan
- Artifact role: 이번 계획 개정의 source prompt
- Runtime baseline: `b2db89e63fc5357b5379783e867f64d165fce739`
- Executor policy: main agent only; no subagent or child execution in this planning step
- Mutation boundary: request artifact와 plan 문서만 수정하며 runtime code, canonical skill, PR, merge, deploy는 건드리지 않는다.
- Chat response: artifact 링크, 계획 링크, 우선순위 요약, 검증 상태와 다음 단계

## Execution Result

- revised plan: `docs/plans/implementation-commit-adaptive-execution/plan.md`
- highest-order goal: `구현커밋` 본문을 90~130줄의 얇은 transaction contract로 줄이고 sequence, scope/Git safety, verified commit evidence 세 책임만 남긴다.
- priority order: verified 4A baseline → Lean 4B/4C/4D → source convergence → clean integration branch → fresh review/PR/merge → canonical skill semantic sync → canary metrics.
- preserved contracts: optional isolated child, exact-path commit, dirty/source-drift protection, wrong-HEAD/manual adoption evidence, secret redaction, child deploy prohibition, main-owned authorized deploy finalization.
- planning verification baseline: Codex Flow related `85 passed`, full `230 passed`; canonical release contracts `5 passed`, canonical full suite `73 passed`.
- execution boundary: this planning step did not implement runtime code, create a PR, merge, deploy, or modify canonical skills.
