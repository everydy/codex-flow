# 해결전략검토 — Main-First Convergence

## 판정

`적절`

## 신뢰도

`High`

## 가장 강한 반대 근거

1. 이 runtime-contract 변경은 current classifier가 high-risk로 올릴 수 있어, 별도 override가 없으면 구현 시작과 동시에 child-default가 다시 켜질 수 있다.
2. canonical skill 저장소의 원본 working tree에는 unrelated dirty 파일이 있으므로 그 경로에서 직접 branch/merge/sync하면 사용자 변경 침범과 activation 불일치가 생길 수 있다.

두 반대는 plan에 current-run main-only override와 clean-worktree PR + overlap/ff-only activation gate를 추가해 해소했다.

## 입증 책임

- causal chain: 충족. 현재 `runner.execute_unit()`은 child implementer와 reviewer를 무조건 생성하고 `retry_eligible()` repair loop를 갖는다.
- root-cause fit: 충족. Lean 4B/4C는 unconditional child, unit review, automatic repair를 직접 제거한다.
- contract preservation: 계획상 충족. exact path, source/HEAD, attempt ledger, 4A isolation, release handoff를 유지한다.
- rollback/safe hold: 충족. 4A code baseline을 삭제하지 않고 optional adapter로 유지하며 canonical activation이 불안하면 held한다.
- test oracle: 충족. main transaction replay/scope, review matrix, atomic final gate, cross-repo activation과 canary 지표가 배정됐다.

## Kill Criteria 해당 여부

`비해당`.

다음은 명시적으로 금지돼 있다: silent fallback, broad suppression, automatic retry, assertion 약화, secret-bearing evidence, mirror overwrite, dirty overlap 무시와 destructive cleanup.

## 핵심 근거

- verified runtime baseline: `b2db89e`, related 85, full 230 tests.
- current code evidence: unconditional child/reviewer construction과 retryable failure model.
- verified canonical skill baseline: `383ea76`, release contracts 5, full 73 tests.
- plan response: explicit pull transaction, `final_only`, typed recovery, atomic final gate, clean integration and semantic sync.

## 주요 리스크

- main self-review는 독립 review가 아니다. plan이 이를 main cumulative review로 정확히 명명했다.
- line target을 hard gate로 쓰면 계약이 잘릴 수 있다. required-contract checklist가 우선한다.
- cross-repo merge 후 installed path activation은 original dirty paths와 겹치면 held해야 한다.
- current `FinalGateRecord`와 new schema의 stale/migration behavior가 구현 test에서 확정돼야 한다.

## 후보 비교

| Candidate | Root-cause fit | Regression surface | Cost | Verdict |
| --- | --- | --- | --- | --- |
| Child 완전 삭제 | 높음 | isolation·fresh review 상실 | 낮음 | 거절 |
| Central adaptive coordinator | 중간 | God object·중복 state machine | 높음 | 거절 |
| Main-first + optional 4A adapter | 높음 | bounded transaction/review changes | 중간 | 채택 |
| Child-per-unit 미세 최적화 | 낮음 | 2N model calls 유지 | 중간 | 거절 |

## 더 나은 대안 또는 축소 가능한 수정 범위

- Commit 4B는 coordinator가 아니라 `begin-main-unit|complete-main-unit|hold-main-unit` 세 transaction command로 제한한다.
- Commit 4C는 `final_only|per_unit` 두 policy만 제공하고 `none`은 만들지 않는다.
- Commit 4D는 exact HEAD·plan·queue·review·test만 담는 최소 record와 one guard로 제한한다.
- external state migration과 evidence cache는 critical path에서 제외한다.

## 구현 전 확인

- Skill Routing Manifest 제목이 revised Commit/Phase 제목과 일치해야 한다.
- current-run override가 queue/source artifact에 persisted돼 child 호출을 방지해야 한다.
- canonical clean worktree와 original installation path의 activation 경계를 별도 test로 고정해야 한다.

## 선제적 진단 로그 계획

| Failure | Boundary/event | Fields | Normal / abnormal | Redaction | Test |
| --- | --- | --- | --- | --- | --- |
| main transaction replay | `main_unit_*` | unit, revision, expected/observed HEAD, reason | exact / stale-conflict | no diff/prompt/secret | replay/owner race |
| policy regression | `delegation_decision` | risk, adapter, review policy, reason | main/final_only / unexplained child | no prompt | policy matrix |
| invalid repair | `recovery_decision` | class, path digest/count, action | explicit main / auto retry | digest only | five classes |
| final evidence corruption | `final_gate_*` | HEAD, plan, revision, status, reason | atomic pass / stale-corrupt | hash only | crash/bypass |
| isolated timeout | existing heartbeat/termination | attempt, phase, elapsed, output age, descendants | heartbeat / orphan | bounded redaction | fake clock/process tree |

## 승인 기준

- ordinary unit child 0 and per-unit reviewer 0
- high-risk adapter fixture remains functional
- automatic retry 0
- main transaction scope/HEAD/revision replay gates pass
- atomic final gate and direct-call bypass tests pass
- canonical release 5 tests and full suite pass after semantic sync
- final blocker/important 0

## 판정 변경 조건

main이 transaction helper를 우회하거나, final gate가 stale/corrupt evidence를 통과시키거나, canonical activation이 dirty overlap/non-FF를 무시하거나, line target 때문에 release contract assertion이 삭제되면 `부적절`로 바뀐다.

## 외부 근거

사용하지 않았다. 이 문제는 로컬 runtime call path, tests, git/worktree ownership으로 인과관계가 닫혀 있어 external threshold를 의도적으로 좁혔다.

## 구현 진행 조건

manifest 제목 정합성과 plan structure validation을 마친 뒤 메인 에이전트만 Commit 4B부터 순차 실행할 수 있다. child/subagent와 parallel run-all은 이번 plan 실행에서 금지한다.
