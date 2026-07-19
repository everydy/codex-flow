# 해결전략검토 — Managed Child Runtime

## 판정

적절

## 신뢰도

High

## 가장 강한 반대 근거

- exact manifest가 완성되기 전에 staged child에서 provisional app-server inventory를 실행하는 것은 새로운 pre-attestation 실행 표면이다. 이 단계가 parent plugin config, MCP, hooks, target repo write authority를 상속하거나 plugin install을 임의 수행한다면 “prepared runtime을 안전하게 만든다”는 causal chain이 끊긴다.
- 계획은 이 반대를 다음 경계로 해소한다: repo-external mode-0700 unique staging, sanitized config, machine-readable hook disable env, no model/exec prompt, `initialize` + cwd-scoped `skills/list`만 허용, target repo Git oracle 무변경, ambiguous ownership deny, exact manifest 생성 후 같은 inventory를 다시 검증, publish 전 validated closure만 atomic rename.
- 이 경계 중 하나라도 구현/테스트에서 빠지면 판정은 즉시 `조건부 적절` 또는 `부적절`로 바뀐다.

## 입증 책임

- failure mode: required closure를 계산한 runner가 prepared runtime을 소유하지 않아 attestation이 env pair 부재에서 선행 실패한다.
- root-cause fit: shared pre-edit boundary가 closure를 준비하고 path를 명시 전달하므로 호출자 env 의존을 직접 제거한다.
- preserved contracts: exact manifest/tree hash, fresh child inventory, plan/runtime/plugin/CLI binding, TTL/nonce, explicit override, profile/custom-provider fail-closed, target repo no-edit.
- rollback: content-addressed managed generation은 실패 시 staging만 제거하고 published prior generation을 교체하지 않는다. explicit override는 validation-only다.
- test oracle: no-env success, same-home metadata, negative launch marker, HEAD/index/worktree digest, cache invalidation/concurrency, focused/full pytest, raw CLI smoke.
- smaller alternative rejection: wrapper-only 또는 attestation 내부 fallback은 entrypoint bypass나 global env race를 남긴다.

## Kill Criteria 해당 여부

비해당.

- error suppression, empty manifest, isolation opt-out, assertion 완화, silent model fallback을 사용하지 않는다.
- 기존 관측성과 error boundary를 제거하지 않고 prepare/reuse/deny 진단을 추가한다.
- API compatibility는 optional explicit parameters + env fallback으로 유지한다.

## 핵심 근거

- `codex_flow/codex_cli.py:552-576`: env pair 부재가 기본 runtime path보다 먼저 실패하는 직접 원인.
- `codex_flow/runner.py:262-320`: required closure와 no-edit attestation의 shared owner.
- `codex_flow/codex_cli.py:140-331`: manifest/path/tree/plugin ownership validator 재사용 가능.
- `codex_flow/codex_cli.py:352-507`: model self-report가 아닌 app-server inventory 정본.
- `tests/test_child_attestation.py:601-626`: implementer 미실행과 Git 무변경 oracle.
- Chronica precedent `prepared_child.py:159-267`: private stage, manifest, discovery, atomic publish 실현 가능.

## 주요 리스크

- provisional app-server가 예상 밖의 plugin/hook side effect를 낼 수 있음.
- child cache plugin payload root derivation이 current CLI directory shape에 과적합될 수 있음.
- content key가 sanitized config/CLI/external/plugin digest 중 하나를 누락하면 stale reuse가 가능함.
- `run_codex_exec()` 또는 resumed review가 explicit prepared home을 전달받지 않으면 attestation/runtime split 발생.
- file lock만 사용하면 same-process thread semantics가 플랫폼마다 다를 수 있음; process lock과 in-process lock을 함께 써야 함.
- source top-level registry symlink와 source 내부 symlink를 구분하지 못하면 데이터 scope가 확장될 수 있음.

## 후보 비교

| 후보 | root-cause fit | 회귀 표면 | 구현 비용 | 검증 비용 | 판정 |
| --- | --- | --- | --- | --- | --- |
| A. runner shared-boundary manager | 높음 — 모든 execution entrypoint 공통 | 중간, 명시 parameter plumbing | 중간 | 중간~높음 | 채택 |
| B. attestation 내부 즉석 provisioning | 중간 — missing env는 해결 | 높음, evidence/mutation 결합 | 중간 | 높음 | 기각 |
| C. wrapper/CLI env injection | 낮음 — raw/programmatic bypass 잔존 | 낮음~중간 | 낮음 | 낮음 | 기각 |
| D. isolation off/empty fallback | 없음 — 증상 은폐 | 매우 높음, security regression | 낮음 | 검증 불가 | Kill |

## 더 나은 대안 또는 축소 가능한 수정 범위

- 채택안은 이미 최소 구조 범위로 좁혀져 있다: manager 신설, inventory record refactor, runner/agent explicit threading, focused tests/docs.
- plugin을 새로 선택/설치하는 기능은 포함하지 않는다. staged child에 실제 나타난 payload만 path로 분류·hash-bind한다.
- external path tree hashing manifest v2는 후속 hardening으로 분리한다.

## 구현 전 확인

- provisional inventory API가 raw `{name, path, enabled, cwd}`를 반환하고 manifest ownership validator와 분리 가능한지.
- staged child app-server가 target repo를 수정하지 않는다는 Git oracle.
- current plugin cache path가 `plugins/cache/<marketplace>/<plugin>/<version>/.../SKILL.md`로 결정적으로 owner root를 찾을 수 있는지; 그렇지 않으면 plugin을 external로 강등하지 말고 deny.
- sanitized config와 machine-readable env가 MCP/global hooks를 포함하지 않는지.
- explicit profile/custom-provider + no pair는 계속 준비 전에 실패하는지.

## 선제적 진단 로그 계획

| 예상 failure mode | 삽입 경계/분기 | event·severity | 필수 field | 정상/이상 기준 | 민감정보·비용 제어 | 검증·제거 조건 |
| --- | --- | --- | --- | --- | --- | --- |
| source resolve 실패 | manager resolve | `managed_child_runtime_denied` warning | stage, required ids, repo fingerprint, error class | unique canonical source / missing·ambiguous·symlink | source 내용·auth·config 원문 금지 | negative unit test; permanent |
| provisional side effect/inventory drift | staged discovery + post-discovery Git oracle | `managed_child_runtime_denied` error | stage, closure fingerprint, unmatched ids, Git oracle flag | repo unchanged + one owner/id / mutation·drift | absolute sensitive paths redaction | fake mutation fixture; permanent |
| cache race/corruption | lock, existing validation, atomic publish | `managed_child_runtime_prepare|reuse|denied` info/warning | cache key prefix, manifest digest, reused, error class | one validated generation / partial·invalid | unit당 1 event, payload 미기록 | concurrency/corruption tests; info 하향 가능 |
| attestation/exec split | agent launch | `managed_child_runtime_denied` error | unit id, attested/exec home fingerprint | equal / mismatch | auth/token/config 미기록 | implementation+review metadata assertion; permanent |

## 승인 기준

- preparation과 exact verification이 branch/dirty handling/implementer보다 먼저 끝난다.
- provisional inventory는 model execution, MCP, target repo mutation 없이 staged child 안에서만 실행된다.
- managed result의 home/manifest가 generate → verify → implementation → review에 동일하게 전달된다.
- explicit pair와 profile/custom-provider 계약이 유지된다.
- no-env raw canonical run이 성공하고 negative cases는 no-edit로 거부된다.

## 판정 변경 조건

- provisional inventory가 repo를 수정하거나 plugin code/hook을 실행한다는 empirical evidence가 나오면 `부적절`로 변경하고 official plugin selection/disable mechanism 없이는 managed auto-prep를 중단한다.
- plugin owner root를 path에서 결정적으로 찾을 수 없으면 해당 id를 external로 자동 허용하지 않는다. 정확한 install manifest가 생길 때까지 `조건부 적절`로 변경한다.
- same-home threading 또는 concurrency oracle이 실패하면 `재계획 필요`다.
- 위 acceptance tests가 모두 통과하면 High/적절을 유지한다.

## 외부 근거

- 외부 웹 근거는 사용하지 않았다. current source, installed Codex CLI `0.144.1`, current tests, local prepared-child precedent가 직접 정본이다.
- external evidence breadth: `threshold intentionally narrowed` — 외부 일반론이 현재 internal contract보다 우선하지 않는다.

## 구현 진행 조건

- 충족. [plan.md](plan.md)의 Commit 1 → Commit 2 → Commit 3을 순차 실행할 수 있다.
- self-preparation 호출부가 연결되는 Commit 2까지만 기존 explicit prepared pair를 bootstrap에 사용한다. Commit 3과 최종 smoke는 env pair 없이 수행한다.
- 구현 중 provisional app-server side effect, ambiguous plugin ownership, same-home split, no-edit oracle failure가 관찰되면 즉시 중단하고 계획을 수정한다.
