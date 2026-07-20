# 해결전략검토 — Implementation Commit Adaptive Execution

## 판정

`조건부 적절` (confidence: High)

Adaptive transactional supervisor는 확인된 root cause를 직접 겨냥하고 기존 격리·attestation·allowed-path·최종 검증 경계를 보존한다. 다만 process-tree cleanup, internal final gate의 네 finalize 경로 배선, legacy state shadow migration이 실제 테스트로 증명되기 전에는 adaptive mode를 기본 활성화하면 안 된다. 따라서 구현은 진행 가능하지만 rollout은 canary/feature flag 상태로 유지한다.

## 가장 강한 반대 근거

가장 큰 위험은 “시간을 줄이기 위해 per-unit review를 줄였는데, final gate 또는 위험도 분류가 틀려 실제 품질·안전 검증도 함께 줄어드는 것”이다. 초안은 이 반대를 이기지 못했다. 보강 계획은 lowering-safe risk, typed verification, transactional adoption, exact-HEAD internal final gate를 함께 활성화하도록 바뀌었지만, 그 배선과 test oracle은 아직 계획일 뿐 실행 증거가 없다.

이 때문에 다음 순서를 바꾸면 전략을 승인할 수 없다.

1. policy/spec와 failure taxonomy
2. atomic ledger와 supervisor
3. 모든 failure path의 transactional adoption
4. exact-HEAD final gate 배선
5. 그 뒤에만 per-unit gate 완화

## 핵심 근거와 causal fit

| 확인된 failure mode | 직접 원인 | 계획의 대응 | 보존되는 계약 |
| --- | --- | --- | --- |
| 최대 900초 blind wait | blocking capture와 heartbeat 부재 | streaming supervisor, heartbeat/output-age 분리 | hard timeout, diagnostics |
| 같은 작업 전체 재실행 | broad retry와 비정규 failure reason | typed failure, canonical fingerprint, 1회 bounded repair | partial evidence, fail-closed |
| child commit 후 failure 시 HEAD guard 우회 | failure catch가 정상 HEAD check보다 먼저 반환 | 공통 finalize + expected-HEAD CAS adoption | orchestrator commit ownership |
| stale `in_progress`와 수동 takeover 불일치 | lease/revision/adoption 정본 부재 | atomic ledger, `held_adoption_required`, adopt CLI | candidate/evidence 보존 |
| 모든 unit full review | 위험도 구분 없는 mandatory review | lowering-safe risk와 policy-aware unit gate | high-risk gate, final review |
| queue/log/handoff 분리 | 직접 write와 revision 부재 | ledger 단일 정본, derived view 재생성 | 기존 human-readable views |
| repo generated state 오염 | repo-local `.codex-flow` | external state + legacy shadow migration | legacy resume/no deletion |

## 입증 책임 판정

- failure mode와 causal chain: `충족`. 코드 경로와 Issue #141 attempt/log timestamp가 같은 패턴을 가리킨다.
- root-cause fit: `충족`. 단순 timeout 증액/축소가 아니라 blocking, retry, adoption, state transaction을 직접 바꾼다.
- contract 보존: `계획상 충족`. high-risk/full gate, exact-HEAD final gate, read-only review, child isolation을 명시했다.
- rollback/safe-disable: `계획상 충족`. strict behavior를 feature flag 기본으로 유지하고 adaptive canary가 실패하면 policy만 strict로 되돌린다. failed candidate와 legacy state는 삭제하지 않는다.
- test oracle: `계획상 충족`. Commit별 stop condition과 final exact-HEAD gate가 있다.
- 축소안 대비 우위: `충족`. heartbeat-only는 safety gap과 중복 review를 해결하지 못하고, parent-direct는 기존 격리 경계를 너무 많이 제거한다.

## Kill Criteria 검사

- broad catch/error suppression/silent fallback: 계획에서 금지됨.
- assertion/test 약화: high-risk와 final gate를 유지하고 lightweight verification을 typed allowlist로 제한함.
- observability 저하: heartbeat, typed event, ledger revision으로 강화함.
- persisted state migration without compatibility/rollback: shadow-copy, provenance, no deletion, strict safe-disable을 명시함.
- retry/timeout만 조정: 해당 없음. retry와 state transition 자체를 바꿈.
- 외부 publish/security를 임의 실행: high-risk로 승격하며 이번 계획 구현 범위 밖.

현재 즉시 `부적절`로 만드는 kill criterion은 없지만 아래 구현 중단 조건이 하나라도 발생하면 adaptive rollout은 거절한다.

## 비교한 해결안

### A. 기존 strict flow + heartbeat만 추가

- 장점: 변경이 작고 빠르다.
- 치명적 한계: mandatory review/repair amplification, failure-path HEAD gap, stale adoption이 그대로다.
- 판정: 임시 관측성 개선으로는 가능하지만 최종 해결책으로 부적절.

### B. child/review 제거 후 메인 에이전트 직접 구현

- 장점: nested 실행 비용을 가장 크게 줄인다.
- 치명적 한계: isolation, attestation, unattended unit boundary와 독립 검토를 한 번에 잃는다.
- 판정: 첫 릴리스 기본안으로 부적절. transactional adoption이 실증된 뒤 docs-only opt-in 실험만 가능.

### C. Adaptive transactional supervisor

- 장점: existing safety boundary를 재사용하면서 blind wait, retry, adoption, state, review overhead를 모두 직접 겨냥한다.
- 비용: 구현 범위와 상태 migration 테스트가 크다.
- 판정: 조건부 채택. 보강된 plan의 순서와 canary 조건을 지켜야 한다.

### D. Persistent child session

- 장점: context reload 비용을 줄인다.
- 치명적 한계: scope bleed와 stale assumption이 생기며 안전 결함을 해결하지 않는다.
- 판정: 현재 보류. Option C 안정화 이후 별도 계측 후보.

## 방향을 바꾼 Findings

1. per-unit gate 완화보다 transactional adoption과 final gate를 먼저 구현하도록 순서를 변경했다.
2. generic runtime의 자동 `review-all-in-one` 호출을 폐기하고 내부 read-only `FinalReviewGate`로 분리했다.
3. plan 문자열 command를 실행하지 않고 trusted typed `VerificationSpec`만 허용하도록 변경했다.
4. state migration을 즉시 이동이 아니라 ledger shadow-copy/parity/no-delete 방식으로 변경했다.
5. 초기 안전 기반에서는 child isolation을 유지한다. 사용자가 요청한 `docs_only` parent-direct는 absence oracle, transactional lease/adoption과 exact final gate가 통과한 뒤 interactive canary로만 연다. unattended `run-all`은 child fallback을 유지한다.
6. 성능 acceptance를 wall-clock 하나가 아니라 deterministic child-call/heartbeat/retry/orphan 지표로 바꿨다.

## 선제적 진단 로그 계획

| Failure mode | Event / severity | 필수 구조화 field | 정상/이상 신호 | Redaction·비용 제어 | 검증/제거 조건 |
| --- | --- | --- | --- | --- | --- |
| child alive but silent | `attempt_heartbeat` / info | attempt, unit, phase, elapsed, output_age, pid-group, ledger_revision | heartbeat 주기 이내 / heartbeat 누락 | payload 없음, 주기 aggregation | fake clock과 silent child; 운영 안정 후 debug field 축소 가능 |
| timeout/cancel/process leak | `attempt_terminated` / warn,error | reason, term_sent, kill_sent, descendant_count, exit | descendant 0 / orphan >0 | argv allowlist id만, 원문 prompt 없음 | TERM-ignore grandchild와 race fixture; orphan 신호는 제거 금지 |
| HEAD/scope drift | `attempt_invariant_failed` / error | expected_head, observed_head, diff_digest, outside_scope_count | equality/0 / mismatch | 파일 내용·token 없음; path는 repo-relative | commit-then-failure fixture; 영구 audit event |
| retry amplification | `attempt_retry_decision` / info,warn | kind, fingerprint, prior_count, allowed | new eligible once / duplicate retry | normalized signature만 | 동일 fingerprint 2회 fixture; 운영 후 sampling 가능 |
| ledger crash/CAS conflict | `ledger_write_recovered` / warn | repo_id, plan_id, expected_revision, observed_revision, recovery | sequential revision / conflict/truncation | state payload 원문 없음 | fault injection; recovery event는 유지 |
| final gate stale/bypass | `final_gate_rejected` / error | command, gate_head, current_head, review_status, verification_status | exact pass / stale, missing, failed | review 본문 대신 artifact hash | 네 finalize command fixture; 제거 금지 |
| evidence cache decision | `evidence_reuse_decision` / info | kind, key_digest, hit, invalidation_reason | deterministic focused hit / forbidden kind hit | env value 없음, allowlisted name+digest | secret canary와 invalidation matrix; hit logs는 rate-limit 가능 |
| legacy state migration | `state_migration_transition` / info,error | repo_id, legacy_path_digest, source_revision, target_revision, phase | shadow parity / mismatch | 절대 경로 대신 digest와 provenance | legacy parity fixture; migration 완료 뒤 info sampling 가능 |

로그는 test oracle이나 assertion을 대체하지 않는다. raw prompt, token, secret-bearing env, 전체 stdout/stderr를 구조화 event에 넣지 않으며 큰 output은 크기 제한·회전된 artifact로만 보존한다.

## 구현 전/중 승인 기준

- Commit 1 승인: unknown risk가 low로 내려가지 않고 unsafe VerificationSpec이 모두 fail-closed한다.
- Commit 2 승인: silent-alive heartbeat와 hard-timeout descendant 0, ledger crash/CAS recovery가 통과한다.
- Commit 3 승인: success/timeout/nonzero 모두 HEAD/scope invariant를 기록하며 automatic rollback/reinstall이 없다.
- Commit 4 승인: docs-only absence oracle/direct lease/adoption, unattended child fallback, 네 finalize 경로의 missing/failed/stale final gate 차단과 review non-mutation이 모두 통과한다.
- Commit 5 승인: legacy state가 삭제되지 않고 source/worktree/absolute path에서 같은 plan을 찾는다.
- Commit 6 승인: final review/CI/native/manual evidence는 cache hit가 불가능하고 secret이 key/log에 노출되지 않는다.
- 기본 전환 승인: exact final HEAD의 blocker/important finding 0, orphan 0, invariant regression 0, duplicate retry 0, deterministic child/review 호출 감소가 확인된다.

## 무엇이 판정을 바꿀 수 있는가

- `적절`로 상향: Commit 2~4의 real subprocess/finalize integration tests와 canary plan에서 process leak·final-gate bypass·finding 품질 저하가 0건임을 증명할 때.
- `부적절`로 하향: network/spec를 fail-open으로 낮추거나, high-risk/final gate를 생략하거나, legacy/candidate evidence를 자동 삭제하거나, final review/CI를 cache reuse할 수 있게 할 때.
- `근거 부족`으로 후퇴: Issue #141 fixture가 실제 기록과 연결되지 않거나 supervisor event가 Codex CLI의 실제 실행 상태를 대표하지 못하는 것으로 확인될 때.

## 구현 진행 조건

보강된 [plan.md](./plan.md)를 exact source로 삼아 Commit 1부터 순차 실행할 수 있다. 한 Commit의 stop condition이 발생하면 뒤 Commit을 억지로 진행하지 않고 strict behavior, 설치 상태와 diagnostics를 그대로 보존한다. Phase 8의 외부 설치 skill 동기화는 codex-flow 최종 merge SHA가 고정된 뒤에만 실행한다.
