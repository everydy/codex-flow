# Refined Request

## Original Request

완료된 `구현커밋` main-first 개편 상태를 다시 확인하고, 앞선 완성형 비교에서 남은 제안이 실제로 필요한지 판정한 뒤 필요할 때만 구현한다. 이미 완료된 설계·병합·canonical skill 동기화는 반복하지 않는다. 연구, 계획, 구현 전 다각도 리뷰와 해결전략 판정을 순서대로 거치고, blocker가 없을 때만 메인 에이전트가 Commit/Phase 순서로 구현한다. 구현 후에는 실제 diff 리뷰와 테스트·커밋·푸시·브랜치 종료까지 검증한다.

## Refined Execution Request

완료된 `implementation-commit-adaptive-execution` 결과를 기준선으로 고정한다. 이번 후속 작업은 로컬 코드와 테스트가 필요성을 입증하는 아래 두 표면만 후보로 둔다.

1. `codex_flow/execution_policy.py`의 자동 위험도 추론이 문서 또는 테스트/fixture의 인용·예시 문구에 포함된 `deploy`, `auth`, `security` 같은 단어만으로 격리 실행과 `per_unit` 리뷰를 불필요하게 승격시키는지 확인하고, 재현 테스트가 성립할 때만 최소 범위로 수정한다.
2. 기존 CLI/dashboard가 이미 가진 queue·attempt·event 정보를 재사용해, 실행 중인 unit의 담당자, phase/status, 경과 시간, 마지막 유의미한 진행, 대기 이유, 현재 검증을 최소한으로 보여줄 수 있는지 확인하고, 실제 데이터 계약이 뒷받침될 때만 기존 텍스트 상태판을 보강한다.

다음은 명시적으로 범위 밖이다.

- Codex Flow 내부에 일반 보조 에이전트용 세 번째 subprocess backend 추가
- 새로운 웹 UI 또는 별도 상태 서비스 구축
- 대규모 token telemetry, prompt/diff 원문 저장, 새 장기 실행 supervisor 설계
- 완료된 main-first, `final_only`, atomic final gate, PR/merge, canonical skill sync의 재구현
- canonical skills 저장소의 현재 unrelated dirty files 수정
- 배포

## Working Brief

- Problem: 남은 두 제안이 실제 결함인지 단순한 이상형 차이인지 확정되지 않았고, 근거 없이 확장하면 구현커밋을 다시 느리고 불투명하게 만들 수 있다.
- Goal: 오탐으로 인한 불필요한 isolated-child/review 비용을 막고, 기존 상태 데이터만으로 블랙박스 대기를 줄인다.
- Hypothesis: path/scope 증거를 자유 텍스트 키워드보다 먼저 해석하고 기존 attempt/queue 상태를 dashboard에 투영하면, 새 실행 backend나 telemetry 시스템 없이 두 문제를 해결할 수 있다.
- Plan: local-only research → plan-first 문서 → review-all-in-one → 해결전략검토 → blocker가 없을 때만 순차 구현 → 최종 리뷰와 테스트 패키징.
- Evaluation:
  - docs-only와 test/fixture-only unit의 위험 단어 예시가 불필요한 `HIGH_RISK` 승격을 만들지 않는다.
  - 실제 high-risk path 또는 product-code action은 계속 `HIGH_RISK`로 승격된다.
  - explicit declared profile과 persisted safety floor는 자동 추론 완화보다 우선한다.
  - dashboard는 active unit의 owner/phase/elapsed/last progress/wait reason/verification을 표시하거나, 데이터가 없을 때 명시적 `unknown`/`not recorded`를 표시한다.
  - 새 backend, 새 daemon, 새 웹 UI, token 원문 저장이 추가되지 않는다.
  - 관련 회귀 테스트와 전체 테스트가 통과한다.

## Execution Notes

- Source baseline:
  - completed plan: `docs/plans/implementation-commit-adaptive-execution/plan.md`
  - prior research: `docs/plans/implementation-commit-adaptive-execution/research.md`
  - completed request artifact: `docs/request-refiner-artifacts/2026-07-21-main-first-codex-flow-convergence-refined-request.md`
- New evidence folder: `docs/plans/implementation-commit-risk-status-followup/`
- Orchestration: `agent-orchestrator` delegation gate result is single-agent; both candidate changes share queue/policy contracts and are small enough that delegation adds coordination cost.
- Execution: main agent only, one Commit/Phase at a time, no `run-all`, no subagent implementation.
- Research: reuse prior research for the completed architecture; add only focused evidence for classifier precedence and status projection. External web research is intentionally excluded unless local contracts prove insufficient.
- Review hard stop: no runtime/test implementation while `review-all-in-one` or `해결전략검토` is active.
- Implementation gate: proceed only if the strategy verdict is `적절`, or `조건부 적절` conditions are fully resolved in the plan before code changes.
- Git boundary: work only in `/Users/moonsoo/projects/codex-flow`; preserve other repositories and unrelated changes.
- Deployment: not authorized.
- Artifact role: this document is the source prompt for all remaining work in this request.

## Execution Result

- Status: implemented; pre-implementation gates passed and final verification completed.
- Implemented commits:
  - `a0b1831`: scope-aware docs/test risk inference with non-lowerable safety floors.
  - `351ccd4`: read-only active owner/phase/elapsed/progress/wait/verification dashboard projection.
  - `949bb4f`: post-review traversal, malformed-state, elapsed and log-redaction hardening.
- Pre-implementation review: blocker 0; two important findings incorporated before implementation.
- Strategy verdict: `적절`, confidence High, kill criteria not applicable.
- Focused verification: 75 passed after final review hardening.
- Full verification: 253 passed before documentation packaging; final same-HEAD rerun is part of canonical test packaging.
- Deployment: not authorized and not attempted.
