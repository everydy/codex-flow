# Pre-implementation Review — Implementation Commit Adaptive Execution

## 1. 구현 판단

`진행 가능` — 단, 이 판정은 보강된 [plan.md](./plan.md)의 Commit/Phase 순서를 그대로 지키는 경우에 한한다.

초안은 확인된 causal chain을 직접 겨냥했지만, runtime finalize 직전의 누적 review 배선, lightweight command의 실행 권한, 명시 호출 전용 `review-all-in-one` 계약이 빠져 `보완 후 진행` 판정이었다. 세 blocker와 9개 important finding을 계획에 반영한 뒤 다시 검토했으며, 현재는 안전 기반(정책·ledger·supervisor·transactional adoption)이 gate 완화보다 먼저 온다. 구현 변경과 fresh test 결과는 아직 없으며 이번 리뷰는 계획/현재 코드에 대한 읽기 전용 구현 전 검토다.

## 2. 상세 검토 결과

### 최초 Blocker와 반영 결과

1. `해결됨 — final runtime gate 부재`
   - 초안 문제: per-unit full review를 줄인 뒤 정상 `run-all/open-pr/create-pr/merge` finalize를 막는 누적 gate가 없었다.
   - 반영: Commit 4에 exact-HEAD `FinalGateRecord`와 네 finalize 경로의 fail-closed 검사를 추가했다. missing/failed/stale record는 finalize를 차단한다.

2. `해결됨 — lightweight command 권한 불명확`
   - 초안 문제: plan 문자열의 focused command를 그대로 실행하면 shell injection, cwd escape, network/dependency install, undeclared mutation 위험이 있었다.
   - 반영: Commit 1에 repo-owned allowlist에서만 해석되는 typed `VerificationSpec`을 추가했다. raw shell, arbitrary executable, cwd escape와 지원되지 않는 network deny는 fail-closed한다.

3. `해결됨 — review-all-in-one 호출 계약 충돌`
   - 초안 문제: generic runtime이 명시 호출 전용 skill을 자동 호출하는 구조가 남아 있었다.
   - 반영: runtime 내부 read-only `FinalReviewGate`를 별도 계약으로 두고, `review-all-in-one`은 active plan/현재 요청이 명시한 경우의 external evidence로만 사용한다.

### 최초 Important finding과 반영 결과

1. `해결됨 — gate 완화 순서`: transactional adoption을 Commit 3으로 앞당기고 Commit 4 feature flag 전까지 strict behavior를 유지한다.
2. `해결됨 — manual takeover API`: `adopt-attempt`의 required arguments, CAS, evidence/HEAD/scope rejection과 nonzero exit를 명시했다.
3. `해결됨 — profile schema`: plan/queue `execution_policy.declared_profile`, default contract, 상향 전용 inference reason/policy version persistence를 명시했다.
4. `해결됨 — repo identity`: git common dir의 non-secret UUID를 stable repo id로 사용해 source/worktree 공유와 separate-clone 분리를 정의했다.
5. `해결됨 — ledger 원자성`: Commit 2부터 ledger를 단일 정본으로 쓰고 derived view revision mismatch는 재생성한다.
6. `해결됨 — failure fingerprint`: kind, phase, normalized signature, expected HEAD, scoped diff digest, policy version을 canonical key로 고정했다.
7. `해결됨 — evidence key/redaction`: declared inputs, config, executable, child manifest, platform, policy, allowlisted env digest를 추가하고 secret 원문 저장을 금지했다.
8. `해결됨 — migration parity`: dashboard/route뿐 아니라 status, morning brief, review, PR, merge, cleanup과 legacy absolute path 회귀를 검증 범위에 추가했다.
9. `해결됨 — diagnostics/process oracle`: malformed/oversized event, truncated JSONL, TERM 무시 grandchild, pipe holder, cancel race, orphan 0을 포함했다.
10. `해결됨 — diagnostics secret redaction`: structured event allowlist, prompt/last-message 원문 금지, stdout/stderr redact-before-write, redactor failure 시 payload drop, 모든 sink의 secret canary 0건 assertion을 추가했다.

### 최초 Minor finding과 반영 결과

1. `해결됨`: 존재하지 않는 `models.py` target을 제거하고 새 타입을 `execution_policy.py`에 소유시켰다.
2. `해결됨`: flaky wall-clock 단독 목표 대신 simulated clock, heartbeat max age, child/review call count, duplicate retry, orphan 수로 benchmark를 정의했다.

### 현재 남은 리스크

- `Minor`: macOS/Linux의 실제 process-tree 종료와 Codex JSON event cadence는 코드만으로 확정할 수 없다. Commit 2 stop condition과 real subprocess fixture가 이를 막는다.
- `Minor`: contract unit에서 per-unit full review가 없어져 finding 발견 시점이 늦어질 수 있다. exact-HEAD final gate에서 누락률을 계측하고 임계치 초과 시 contract를 full gate로 되돌리는 safe-disable이 필요하다.

### Seven-solution delta review

- 최초 delta review는 profile schema 불일치와 parent-direct 구현 단위 부재를 blocker로 판정했다.
- 보강 후 plan은 profile을 `docs_only|contract|high_risk` 하나로 통일하고, docs-only absence oracle, interactive direct lease/adoption, unattended child fallback과 테스트를 Commit 1·4에 배정했다.
- 30/60/120/180 state machine의 fake-clock/reset/liveness oracle을 Commit 2에 배정했다.
- fetch/base sync/HEAD freeze/tests/review/attestation/same-HEAD CI 순서와 관련 `git_ops.py`, `pr.py`, `merge.py` 테스트를 Commit 6에 배정했다.
- stable docs/GitHub Issue·PR/main CI의 상태 소유권과 merge 기록용 후속 PR 금지 validator를 Commit 7에 배정했다.

`DELTA_REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="The amended plan consistently defines the execution profiles, docs-only direct lease/adoption and unattended fallback, timing-boundary tests, Commit 6 preflight ownership, and Commit 7 status ownership."`
- `Minor`: network deny는 OS별 강제 수단이 없을 수 있다. 지원이 확인되지 않은 platform에서는 `network=deny` spec을 실행하지 않고 held 처리해야 하며 `inherit`로 조용히 낮추면 안 된다.

### 필수 테스트 공백 — 계획에 배정됨

- policy/spec: risk spoof, unknown risk, shell metacharacter, arbitrary executable, dependency install, network, cwd/symlink escape, undeclared mutation.
- supervisor: silent-alive, slow valid events, malformed/oversized output, TERM 무시 descendant, stdout pipe holder, cancel-timeout race, orphan 0.
- ledger/adoption: crash-at-write-stage, stale CAS, duplicate finalize, commit-then-timeout/nonzero, manual evidence mismatch.
- final gate: `run-all/open-pr/create-pr/merge`의 missing/needs_work/stale HEAD 차단과 fresh pass.
- state: source/worktree identity, symlink/rename/separate clone/no remote, legacy absolute path와 모든 finalize/cleanup parity.
- evidence: untracked/config/lockfile/tool/runtime/policy/platform/env 변화별 miss, secret 비노출, final review/CI cache hit 불가.

## 3. 다음 task

1. [plan.md](./plan.md)의 Commit 1부터 Commit 7까지 `구현커밋`으로 순차 실행한다. 병렬 run-all은 사용하지 않는다.
2. 각 Commit의 stop condition을 gate로 사용한다. 특히 Commit 2 process-tree cleanup, Commit 3 failure-path adoption, Commit 4 finalize fail-closed가 통과하기 전에는 adaptive gate를 활성화하지 않는다.
3. Commit 1~7의 exact final HEAD에서 명시된 `review-all-in-one`, 내부 `FinalReviewGate`, 전체 repository test와 deterministic benchmark를 새로 실행한다.
4. merge 이후에만 별도 Phase 8로 설치된 skill을 동기화하며, 외부 dirty 저장소의 다른 변경은 건드리지 않는다.

`REVIEW_GATE status="pass" blockers=0 important=0 minor=3 reason="The original structural blockers, nine important contract gaps, and the follow-up diagnostics-redaction gap are incorporated into the plan; implementation may proceed sequentially with the documented stop conditions and no adaptive gate activation before safety foundations pass."`
