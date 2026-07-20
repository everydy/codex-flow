# Pre-implementation Review — Main-First Convergence

## 1. 구현 판단

`보완 후 진행 → findings 반영 완료`.

검토 대상은 current `plan.md`와 runtime baseline `b2db89e`다. 구현 전 검토이므로 Lean 4B 이후 동작은 아직 구현되지 않았다. 현재 코드에서 `runner.execute_unit()`이 child runtime·implementer·reviewer를 무조건 만들고, `FailureRecord.retryable`과 repair loop가 남아 있다는 사실은 계획의 root cause와 일치한다.

최초 검토의 blocker 1개와 important 5개를 계획에 반영했다. 이 반영은 구현 승인이 아니라 다음 `해결전략검토` 입력이다.

## 2. 상세 검토 결과

### Blocker — 반영 완료

1. main-first transaction 경계 부재
   - 문제: 계획의 `main_handoff`는 Codex Flow가 parent/main을 호출해 기다리는 push 구조처럼 읽혔고 실제 API·ownership·CAS가 없었다.
   - 근거: 현재 `runner.execute_unit()`은 subprocess child를 직접 생성하는 단일 경로뿐이다.
   - 반영: main이 `begin-main-unit`으로 contract를 받고 직접 수정한 뒤 `complete-main-unit`으로 exact commit하는 pull 구조와 `hold-main-unit` recovery 경계를 Commit 4B에 고정했다.

### Important — 반영 완료

1. main-only final review를 independent라고 부르던 모순
   - 반영: ordinary path는 `main cumulative review`로 명명하고, 실제 fresh-context 독립성이 필요한 경우만 isolated review로 승격한다.
2. atomic final-gate write 누락
   - 반영: temp write, fsync, atomic rename과 corrupt record fail-closed test를 Commit 4D에 추가했다.
3. canonical `테스트/SKILL.md` 불필요 수정 위험
   - 반영: Phase 8의 write target에서 제외하고 deploy ownership의 read-only baseline으로 고정했다.
4. 90~130줄 목표가 필수 계약 삭제를 유도할 위험
   - 반영: required-contract checklist를 line target보다 우선하는 stop condition을 추가했다.
5. 진단 이벤트와 replay/crash test 공백
   - 반영: main transaction, delegation, recovery, final gate, isolated heartbeat에 대한 최소 event/field/redaction/oracle 표를 추가했다.

### Minor

1. existing `ExecutionProfile/ExecutionMode/FailureKind` migration names and compatibility serialization은 Commit 4B 구현 전에 exact schema test로 잠가야 한다.
2. current `FinalGateRecord`는 HEAD와 bool만 가지므로 new record migration/read compatibility 또는 explicit invalidation 정책이 구현에 필요하다.

## 테스트 공백 배정

- Commit 4B: stale/replayed queue revision, competing owner, tracked/untracked out-of-scope drift, ordinary child call 0.
- Commit 4C: `final_only/per_unit` matrix, arbitrary verification rejection, reviewer mutation/resume/repair 거부.
- Commit 4D: missing/failed/stale/corrupt/pass, crash-at-write, direct library bypass, secret canary.
- Phase 8: reference-link validator, required-contract checklist, 90~130 target, canonical release 5 tests.
- Phase 9: ordinary child 0, main cumulative review 1, high-risk isolation 유지, automatic retry/orphan 0.

## 코드 스니펫 검토

- Commit 4B snippet은 callback-style adapter에서 explicit transaction API로 교체돼 구현 방향이 명확해졌다.
- Commit 4C snippet은 current policy schema에 추가할 최소 review decision을 표현한다.
- Commit 4D snippet은 consumer만 보여주므로 구현 시 atomic producer test가 반드시 함께 있어야 한다.

## 3. 다음 task

1. 보강된 `plan.md`를 `해결전략검토`로 읽기 전용 검토한다.
2. blocker 또는 방향 전환 finding이 나오면 plan에 다시 반영한다.
3. 최종 판정이 `적절`이고 blocker가 0일 때만 메인 에이전트가 Commit 4B를 시작한다.

`REVIEW_GATE status="pass" blockers=0 important=0 minor=2 reason="The plan now defines an explicit main-owned transaction API, distinguishes main cumulative review from independent isolation, preserves the canonical test skill, requires atomic final-gate evidence, and assigns the missing replay/crash/redaction tests."`
