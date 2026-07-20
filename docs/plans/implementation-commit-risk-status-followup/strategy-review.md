# 해결전략검토 — Implementation Commit Risk And Status Follow-up

## 판정

적절

## 신뢰도

High

## 가장 강한 반대 근거

- `tests/` 아래의 파일도 외부 배포나 인증 경계를 직접 호출할 수 있으므로, 경로만으로 위험 단어를 무조건 무시하면 false negative가 생길 수 있다.
- dashboard가 queue와 ledger를 함께 읽으면 서로 다른 revision의 상태를 한 화면에 합쳐 stale한 설명을 만들 수 있다.

두 반대는 구현을 막지는 않는다. 첫째, 제안은 test-rooted unit을 `docs_only`가 아닌 `contract`로 유지하며 arbitrary verification을 허용하지 않는다. declared/persisted `high_risk`, explicit isolated adapter와 per-unit review도 그대로 우선한다. 둘째, dashboard는 acceptance나 state transition을 결정하지 않는 read-only operator view이고, ledger revision/queue fallback을 함께 표시해 증거 수준을 구분할 수 있다.

## 입증 책임

- failure mode: free-text risk word가 allowed-path scope보다 먼저 평가되어 docs/test examples가 isolated-child/per-unit으로 승격된다.
- causal chain: false-positive `HIGH_RISK` → child process + full unit gate + per-unit AI review → 불필요한 wall time/token cost.
- root-cause fit: automatic inference 순서만 scope-aware하게 바꾸며 explicit/persisted floor는 손대지 않는다.
- status causal chain: queue/ledger에 상태가 있으나 dashboard가 투영하지 않아 사용자가 수동 파일 탐색을 해야 한다.
- blast radius: production code 2개 파일과 focused tests; schema, adapter, supervisor, final gate 변경 없음.
- rollback: 두 commit을 일반 revert하면 데이터 migration 없이 원래 동작으로 복귀한다.
- test oracle: plan과 pre-implementation review의 policy/dashboard matrix plus full pytest suite.

## Kill Criteria 해당 여부

비해당

- error suppression, broad catch로 runtime 실패를 숨기지 않는다. dashboard의 corrupt-ledger fallback은 read-only view 실패만 격리하고 runtime ledger를 변경하지 않는다.
- assertion/test/schema/observability를 약화하지 않는다.
- retry, timeout, cache 또는 dependency를 변경하지 않는다.
- auth/deploy 권한이나 persisted data contract를 변경하지 않는다.

## 핵심 근거

- local probe로 docs/test false positives와 product true positive가 재현됐다.
- `classify_execution_policy()`의 outer profile maximum이 declared/persisted safety floor를 별도로 보존한다.
- queue와 attempt ledger가 owner, phase, elapsed/progress, failure and verification 정보를 이미 제공한다.
- 구현은 기존 ownership boundary 안에서 각각 inference와 presentation만 바꾼다.

## 주요 리스크

- traversal/absolute path가 test oracle을 속일 수 있음: 구현 전 guard와 tests가 plan에 추가됐다.
- mixed tests/product scope가 완화될 수 있음: 모든 경로가 safe scope일 때만 absence oracle을 통과시킨다.
- raw wait reason 노출: existing redaction + one-line + length cap을 요구한다.
- queue/ledger 불일치: dashboard에 source/fallback 수준을 표시하고 acceptance 판단에는 사용하지 않는다.
- elapsed precision: legacy/main fallback은 queue update 기준임을 명시한다.

## 후보 비교

| 후보 | root-cause fit | 회귀 표면 | 구현 비용 | 검증 비용 | 판정 |
| --- | --- | --- | --- | --- | --- |
| A. 문서/운영 지침만 추가 | 낮음 | 낮음 | 낮음 | 낮음 | 부적절 |
| B. quote parser + 새 status persistence | 중간 | 높음 | 높음 | 높음 | 부적절 |
| C. scope-aware inference + read-only projection | 높음 | 낮음 | 낮음 | 중간 | 적절 |
| D. 새 backend/telemetry service | 낮음(현재 결함 대비) | 매우 높음 | 매우 높음 | 매우 높음 | 부적절 |

## 더 나은 대안 또는 축소 가능한 수정 범위

- 승인 범위가 이미 최소다. `execution_policy.py` 자동 inference와 `dashboard.py` presentation만 수정한다.
- suggested command, runner writers, queue schema, ledger schema, canonical skill, README는 수정하지 않는다.
- token 계측은 만들지 않고 이후 1–2개 실제 run에서 wall time/child call/review count만 별도 관찰한다.

## 구현 전 확인

- current branch/default/upstream/dirty 상태 확인.
- plan의 두 Commit만 route하고 main transaction으로 순차 실행.
- Commit 1 전에 false-positive and safety-floor tests를 먼저 추가.
- Commit 2에서 queue/ledger byte immutability test 포함.
- 모든 focused tests와 full suite가 기존 baseline 이상인지 확인.

## 선제적 진단 로그 계획

| 예상 failure mode | 삽입 경계/분기 | event·severity | 필수 field | 정상/이상 기준 | 민감정보·비용 제어 | 검증·제거 조건 |
| --- | --- | --- | --- | --- | --- | --- |
| false-negative policy | 기존 policy result/reasons | 새 event 없음 | existing effective profile/reason | product scope high / unexpected contract | prompt 원문 미저장 | focused matrix |
| corrupt/missing ledger | dashboard read boundary | 새 event 없음; display fallback | path class, availability only | render continues / no crash | raw JSON 미표시 | corrupt fixture |
| stale queue/ledger view | dashboard display | 새 event 없음 | queue status, ledger phase/revision when present | evidence source visible | no payload | mismatched fixture |
| leaked wait reason | dashboard sanitizer | 새 event 없음 | bounded redacted string | single line/capped / token absent | existing redaction | secret-like multiline fixture |

새 운영 로그는 필요하지 않다. 기존 policy reasons, queue timestamps와 attempt ledger가 원인 진단에 충분하며 새 로그는 중복 상태가 된다.

## 승인 기준

- required test matrix passes.
- no edit outside planned production/test/docs files.
- profile floors and verification allowlist remain unchanged.
- dashboard rendering is byte-for-byte non-mutating for queue/ledger inputs.
- final review blocker/important findings are zero.

## 판정 변경 조건

- tests-rooted unit이 실제 외부 action 권한을 자동 획득한다는 코드 경로가 발견되면 `조건부 적절`로 낮추고 oracle을 더 좁힌다.
- queue/ledger에서 요청된 status를 안전하게 읽을 수 없으면 Commit 2를 중단하며 새 schema를 이번 범위에 억지로 추가하지 않는다.
- existing safety-floor regression이나 full-suite failure가 발생하면 `부적절`로 전환하고 구현을 hold한다.

## 외부 근거

- 없음. repository-local policy precedence와 state contract가 원인을 완전히 정의하므로 외부 runtime/version 근거가 필요하지 않다.

## 구현 진행 조건

- 현재 판정은 `적절`, blocker 0, 재계획 필요 없음이다.
- 사용자의 이번 요청이 사전 승인 조건을 명시했으므로 별도 재질문 없이 계획의 Commit 1 → Commit 2 → Phase 3 순서로 진행할 수 있다.
- 구현 단계에서는 이 read-only gate를 종료하고 `plan-first-implementation`/`구현커밋` 실행 계약으로 전환한다.
