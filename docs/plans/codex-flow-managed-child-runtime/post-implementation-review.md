# 구현 후 통합 리뷰 — Managed Child Runtime

## 판정

통과. blocker 0, important 0, minor 0.

## 실제 변경 계약

- `codex_flow/child_runtime.py`가 required skill closure를 repo 외부 private staging에 준비하고, exact inventory·manifest·content key를 검증한 뒤 원자적으로 publish/reuse한다.
- explicit `CODEX_FLOW_CHILD_HOME` + `CODEX_FLOW_CHILD_MANIFEST` pair는 validation-only 호환 경로로 유지한다. half pair, parent home, repo-local home, explicit profile/custom provider의 무준비 실행, isolation 해제는 edit 전에 거부한다.
- `runner.execute_unit`이 branch/dirty handling/implementer보다 먼저 managed runtime을 준비·attest하고, 동일한 home을 implementation과 resumed review에 명시 전달한다.
- 준비·검증 실패는 `needs_work`, `changed_paths=[]`, implementer 미실행을 유지한다.

## 회귀·보안 검토

- target repo mutation-before-attestation: 테스트로 차단됨.
- process-global env mutation: 도입하지 않음. runtime path를 parameter로 전달함.
- cache stale/race: source/config/CLI/plugin/external inventory를 key에 포함하고 thread lock + file lock + atomic rename을 사용함.
- credential boundary: auth 내용은 복사하지 않고 parent `auth.json` symlink만 사용하며 진단에는 payload를 기록하지 않음.
- plugin/외부 skill ownership: deterministic child plugin cache root만 hash-bound하고 ambiguous child-local path는 거부함.
- 기존 manifest/attestation exact closure, TTL, nonce, CLI/plan/runtime bindings는 유지됨.

## 실행 증거

- Commit 1: `d95e75f` — deterministic managed preparation.
- Commit 2: `1d22893` — attestation/implementation/review same-home threading.
- Commit 3: `c67386c` — 운영 계약, raw no-env fixture/proof.
- 무환경변수 실제 Commit 3 실행: `event=prepare source=managed`, 관리형 home 생성 후 implementer 진입.
- timeout 복구 실행: 동일 cache key/home으로 `event=reuse source=managed`, 보존된 diff를 repair attempt에서 완료.
- 전체 테스트: `151 passed in 42.79s`.
- diff integrity: `git diff c8f5082..HEAD --check` 통과.
- Codex Flow review: plan ready, 3개 unit 모두 done, source drift clean.
- 원본 작업 브랜치 통합: merge commit `ee39ef6`; 임시 task worktree/branch 정리 완료.
- 통합 후 독립 raw no-env smoke: 새 disposable repo/runtime에서 `event=prepare`, review score 100, `action: done`.
- 통합 후 전체 테스트 재실행: `151 passed in 38.62s`.

## 테스트 공백과 잔여 위험

- external skill tree 자체의 hash binding은 manifest schema v1의 기존 한계다. 이번 변경은 이를 완화하지 않고 기존 exact inventory 검증을 유지했으며, external tree hashing은 schema v2 후속 hardening 대상이다.
- 실제 Codex app-server/plugin 동작은 설치된 CLI `0.144.1`에서 검증했다. 향후 CLI inventory schema가 바뀌면 content key가 달라지고 준비가 fail-closed로 끝나야 한다.
- UI/브라우저 표면이 없는 CLI/runtime 변경이므로 localhost·배포 링크 검증은 해당하지 않는다.

## 최종 Gate

`REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="managed preparation, exact attestation, same-home execution, no-edit denial, cache reuse, raw no-env CLI proof, and 151-test regression suite passed"`
