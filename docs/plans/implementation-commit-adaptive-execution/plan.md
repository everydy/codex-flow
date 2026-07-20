# Implementation Commit Main-First Convergence Plan

## North Star

`구현커밋`을 거대한 child 실행기가 아니라 다음 세 가지에 집중하는 얇은 계약으로 만든다.

1. 승인된 계획을 `Commit/Phase` 순서대로 진행한다.
2. 변경 범위와 Git 상태를 안전하게 보존한다.
3. 검증된 결과를 정확히 커밋하고 완료 상태를 증명한다.

최종 `구현커밋/SKILL.md` 본문 목표는 약 90~130줄이다. worker 선택은 `agent-orchestrator`, 실제 구현은 기본적으로 메인 에이전트, 격리 child는 high-risk·unattended·fresh-context 검증처럼 명확한 필요가 있을 때만 사용한다.

## Goal

Commit 4A에서 검증한 격리 실행과 read-only reviewer 분리는 보존하되 기본 경로에서는 제거한다. Lean 4B·4C·4D로 main-first, `final_only`, 수동 recovery, 최소 cumulative final gate를 구현한 뒤 원격 `main`과 로컬 누적 변경을 깨끗한 통합 브랜치에서 수렴하고 PR·병합·canonical skill semantic sync까지 이어간다.

## Verified Baseline

- runtime: `/Users/moonsoo/projects/codex-flow`
- active branch: `codex/runtime-active-implementation-commit-b2db89e`
- verified baseline: `b2db89e63fc5357b5379783e867f64d165fce739`
- Commit 4A related tests: `85 passed`
- Codex Flow full tests: `230 passed in 336.38s`
- canonical skills HEAD: `383ea76a0e491d9ed007d724d7e555350b2d9d1e`
- canonical release contracts/full tests: `5 passed` / `73 passed`

`b2db89e`는 rollback하거나 다시 구현하지 않는다. 4A는 optional `isolated-codex-child` adapter의 안전 기반으로 고정한다.

## Execution Progress

- Lean 4B complete: `0089580` (`118` related regression tests passed before commit)
- Lean 4C complete: `dfad52c` (`final_only` default and final-review unit removal)
- Lean 4D complete: `27e9069` (atomic cumulative final gate and direct merge bypass block)
- Phase 5 provenance: [convergence-manifest.md](convergence-manifest.md)
- `origin/main` convergence: linear ancestor, remote-only divergence `0`

## Non-Negotiable Simplification

`구현커밋` 기본 본문과 기본 경로에서 제거한다.

- 모든 Commit의 child 강제 실행
- 모든 Commit의 `review-all-in-one`
- `needs_work` 자동 재실행
- final review 문서를 별도 구현 unit으로 취급하는 방식
- child runtime의 상세 구현 설명
- PR·병합·배포·cleanup의 긴 내부 절차
- 900초 동안 진행 상태를 알 수 없는 blind wait
- worker가 자기 구현을 직접 승인하는 구조

상세 계약은 각각 `agent-orchestrator`, Codex Flow runtime reference, canonical `테스트`, recovery reference로 분리한다. `구현커밋` 본문에는 routing·sequence·scope·commit contract만 남긴다.

## Canonical Defaults

- `execution_owner: main`
- `orchestration_mode: fixed-workflow`
- `delegation: gated`
- `worker_cap: 2`
- `implementation_parallelism: 1`
- `executor_adapter: auto`
- `review_policy: final_only`
- `automatic_repair: false`
- `recovery_owner: main`

`auto`의 기본값은 main이다. plan metadata가 high-risk/isolation-required를 명시하거나 사용자가 직접 요구할 때만 `isolated-child`와 `per_unit`으로 승격한다.

이번 계획 실행에는 현재 사용자 요청이 우선하는 explicit override를 적용한다.

- `executor_adapter: main`
- `review_policy: final_only`
- `delegation: disabled_for_implementation`
- `recovery_owner: main`

따라서 Lean 4B~4D와 후속 통합은 메인 에이전트만 수행한다. 이 override는 향후 제품 runtime의 high-risk 기본 정책을 낮추지 않으며, high-risk isolated adapter는 fixture로만 검증한다.

## Priority Map

| Priority | Work | Completion signal |
| --- | --- | --- |
| P0 | 4A와 정본 SHA 잠금, 계획 재구성 | 4A rollback 없음, source와 contract 일치 |
| P1 | Lean 4B·4C·4D | main-first, `final_only`, automatic repair 0, final gate |
| P2 | origin/main과 누적 변경 수렴 | transplant manifest와 clean integration branch |
| P3 | fresh review·CI·PR·merge | same-HEAD green, blocker/important 0, merged SHA |
| P4 | canonical skill semantic sync | 90~130줄 목표와 release handoff 동시 보존 |
| P5 | canary·지표·후속 hardening | 일반 작업 child 0, final review 1, invariant regression 0 |

## Responsibility Split

| Owner | Responsibility |
| --- | --- |
| `plan-first-implementation` | Commit/Phase, risk, allowed paths, acceptance oracle |
| Main agent | 기본 implementer, 유일한 integrator·recovery owner·final acceptance owner |
| Codex Flow | source drift, worktree, scope, exact commit, queue, evidence, finalization |
| `agent-orchestrator` | delegation gate, worker card, budget, cancel/supersede/takeover |
| Optional isolated child | 명시적 isolation·unattended·fresh-context 작업만 |
| `테스트` | commit/push, PR/merge, authorized deploy와 Production Convergence Gate |

Main-first는 Codex Flow가 parent/main을 subprocess로 호출하거나 기다리는 구조가 아니다. 메인 에이전트가 transaction API를 직접 호출하는 pull 구조다.

1. `begin-main-unit`: ready unit, expected HEAD, allowed paths, initial status digest와 queue revision을 반환하고 owner를 `main`으로 잠근다.
2. 메인 에이전트: 해당 계약 안에서 직접 수정하고 검증 evidence를 만든다.
3. `complete-main-unit`: expected revision/HEAD/scope/evidence를 다시 검증하고 exact paths만 stage·commit한다.
4. 실패 시 `hold-main-unit`: candidate와 evidence를 보존하고 main recovery decision을 기록한다.

## Conflict And Gap Matrix

| Existing direction | Problem | New decision |
| --- | --- | --- |
| child implementer가 기본 | context·model call 반복 | main이 기본, 4A는 optional adapter |
| unit마다 full review | token·wall-clock 중복 | `final_only` 기본 |
| `retryable=True` repair | scope mismatch도 전체 재실행 | automatic repair off, main recovery |
| large central coordinator | God object 위험 | 얇은 dispatcher + existing transaction helpers |
| final evidence가 구현 unit | 제품 repair 권한과 충돌 | canonical verification 뒤 deterministic writer |
| mirror를 설치본에 sync | 최신 release handoff 유실 | semantic merge only |
| 누적 branch를 바로 PR | unrelated history 포함 위험 | origin/main 기반 clean integration |

## Delegation And Review Gates

child/worker는 다음이 모두 true일 때만 사용한다: 객관적 검증 가능, 실제 시간/전문성/격리 이득, 분리 가능한 read/write set, 사전 정의된 acceptance signal. 하나라도 아니면 main이 직접 수행한다.

| Policy | Unit gate | Independent review |
| --- | --- | --- |
| `final_only` 기본 | typed deterministic check + scope/diff | 전체 완료 뒤 main cumulative review 1회 |
| `per_unit` high-risk/명시 요청 | typed check | unit마다 4A read-only reviewer + final 1회 |

`review-all-in-one`은 generic runtime이 자동 호출하지 않는다. 사용자가 명시한 구현 전·최종 검토에만 적용한다.

`final_only`의 main cumulative review는 같은 main agent가 수행하므로 “독립 리뷰”라고 부르지 않는다. fresh-context 독립성이 acceptance requirement인 작업은 delegation gate를 통과해 4A reviewer를 사용하는 `per_unit` 또는 explicit final isolated review로 승격한다.

## Failure And Recovery Contract

failure class는 `repairable_in_scope`, `repairable_new_scope`, `environment`, `operator`, `protocol` 다섯 가지다.

same-unit retry는 `repairable_in_scope`이고 `repair_paths`가 allowed paths/write set의 부분집합이며 같은 fingerprint가 아니고 main recovery owner가 명시 승인한 경우에만 가능하다. evaluator는 finding만 반환하고 verifier는 제품 코드를 수정하지 않으며 recovery-specialist는 자동 retry 권한을 갖지 않는다.

상태 흐름: `planned -> preflighted -> implementing(main|isolated-child) -> deterministic_unit_gate -> optional_read_only_review -> main_recovery_decision -> exact_path_commit -> fresh_cumulative_review -> canonical_tests -> evidence_writer -> FinalizeGuard -> PR/merge/authorized deploy by main`.

## Minimal Final Gate

Proposed record: `FinalGateRecord(reviewed_head, plan_digest, terminal_queue_revision, cumulative_review_status, canonical_test_status, evidence_hash)`.

- HEAD, plan 또는 queue revision이 바뀌면 stale이다.
- cumulative review와 canonical tests가 모두 pass여야 한다.
- native/manual/remote CI evidence는 cache로 재사용하지 않는다.
- prompt, diff, token, cookie, credential, secret 원문은 기록하지 않는다.
- write는 temp file → fsync → atomic rename으로 수행하고 crash/corrupt record는 pass로 읽지 않는다.

## 선제적 진단 로그 계획

| Failure mode | Boundary/event | Required fields | Safe signal / failure signal | Redaction and cost | Verification |
| --- | --- | --- | --- | --- | --- |
| main transaction stale/replay | `main_unit_opened|main_unit_rejected|main_unit_completed` | plan/unit id, queue revision, expected/observed HEAD, reason, elapsed | revision·HEAD 일치 / stale·owner conflict | path content, diff, credential 금지 | replay, competing owner, HEAD drift fixture |
| wrong adapter/review policy | `delegation_decision` | unit id, risk, adapter, review policy, reason | default main/final_only / unexplained child or downgrade | user prompt 원문 금지 | ordinary/high-risk policy matrix |
| invalid recovery | `recovery_decision` | failure class, repair-path digest, action, owner | main-approved bounded repair / auto retry | path list 대신 digest와 count | all five failure classes, duplicate fingerprint |
| final gate stale/corrupt | `final_gate_written|final_gate_rejected` | HEAD, plan digest, queue revision, statuses, reason | atomic exact match / missing·stale·corrupt | evidence hash만, output 원문 금지 | crash-at-write and bypass matrix |
| isolated child blind wait | existing heartbeat/termination events | attempt, phase, elapsed, output age, descendants | periodic heartbeat / hard deadline·orphan | bounded redacted output | simulated clock and descendant cleanup |

main-first 경로는 장기 child wait가 없으므로 unit phase transition만 기록한다. isolated adapter의 30초 heartbeat와 process cleanup 증거는 4A 이전 안전 기반을 유지한다.

## Release Handoff Contract To Preserve

1. commit-unit child의 `Do not deploy`는 유지한다.
2. child가 반환하면 main이 release finalization 소유권을 맡는다.
3. 명시 승인된 deploy는 canonical `테스트`의 Deploy Workflow와 Production Convergence Gate까지 진행한다.
4. deploy 권한을 keyword match로 추론하지 않는다.
5. release evidence는 authorization/source/deploy-source/artifact/deployment/live assertion을 남기고 secret은 남기지 않는다.
6. Codex Flow mirror를 canonical skill에 통째로 복사하지 않는다.

## Existing Plan Disposition

| Item | Disposition |
| --- | --- |
| 4A reviewer separation | 완료·보존: optional isolated adapter |
| 기존 4B profile coordinator | Lean 4B로 대체 |
| 기존 4C large producer | Lean 4C·4D로 축소 분리 |
| 기존 4D activation matrix | 최소 FinalizeGuard만 P1, rollout은 P5 |
| Commit 5 external state | P5 hardening으로 연기 |
| Commit 6 evidence cache | critical path에서 제거, 필요 시 P5 |
| Commit 7 docs/skill drift | Phase 8 semantic sync로 축소 |
| Phase 8 byte sync | 제거, semantic merge로 대체 |
| Phase 9 final/rollout | Phase 7 merge gate와 Phase 9 canary로 분리 |

## Skill Routing Manifest

| Phase | Required skills | Optional skills | Evidence |
| --- | --- | --- | --- |
| Commit 4B: Main-first transaction and recovery ownership | `agent-orchestrator`, `구현커밋` 계약 참조 | `superpowers:test-driven-development` | main-first와 recovery state tests |
| Commit 4C: Final-only and deterministic unit gate | `plan-first-implementation` | `superpowers:test-driven-development` | review policy와 deterministic gate |
| Commit 4D: Cumulative final review and minimal merge gate | `테스트` 계약 참조 | `qa-gate` | final gate와 release handoff tests |
| Phase 5: Build the source convergence manifest | `구현커밋` 계약 참조 | 없음 | source/commit provenance |
| Commit 6: Create the clean integration branch | `테스트` | `content-sync-auditor` | clean integration diff |
| Phase 7: Fresh review, PR, CI and merge | `review-all-in-one`, `테스트` | `qa-gate` | fresh review, CI, merged SHA |
| Phase 8: Shrink and semantically sync canonical implementation skill | `테스트` | `content-sync-auditor` | canonical contract tests |
| Phase 9: Canary and acceptance metrics | `테스트` | `qa-gate` | canary metrics |

## Implementation Plan

### Commit 4B: Main-first transaction and recovery ownership

- priority: P1
- target: new `main_unit.py`, `execution_policy.py`, `runner.py`, `cli.py`, `plans.py`, `attempt_ledger.py`, focused tests
- change: default main execution, optional adapter, automatic repair removal, five failure classes, and explicit `begin-main-unit|complete-main-unit|hold-main-unit` transaction API
- proposed snippet: `contract = begin_main_unit(plan, unit, expected_revision); ...; complete_main_unit(contract, evidence)`
- verify: ordinary child call 0, explicit high-risk child call 1, out-of-scope retry 0, stale/replayed revision rejection, competing owner rejection, tracked/untracked scope drift rejection
- success: unconditional child call이 사라지고 4A tests는 유지된다.
- stop: main이 scope/commit helpers를 우회하거나 automatic retry가 남는다.
- tradeoff: 세 transaction command는 추가하지만 main을 기다리는 coordinator나 parent callback은 만들지 않는다.

### Commit 4C: Final-only and deterministic unit gate

- priority: P1
- target: `execution_policy.py`, `runner.py`, `reviewer_agent.py`, verification registry, focused tests
- change: default `final_only`, explicit `per_unit`, typed unit check, high-risk-only reviewer, final evidence unit 제거
- proposed snippet: `review_required = policy.review_policy == PER_UNIT or policy.high_risk`
- verify: policy matrix, arbitrary command rejection, reviewer non-mutation, evidence-unit absence
- success: 일반 3-unit 계획의 unit reviewer 0, main cumulative review 1; high-risk는 unit+final isolated review 유지
- stop: `none` review, automatic `review-all-in-one`, verifier product mutation
- tradeoff: 일부 finding이 final에 발견될 수 있어 typed checks와 high-risk 승격을 유지한다.

### Commit 4D: Cumulative final review and minimal merge gate

- priority: P1
- target: `final_gate.py`, `run_all.py`, `pr.py`, `merge.py`, `cli.py`, tests
- change: terminal exact HEAD에서 main cumulative review와 canonical tests 1회, atomically written small FinalGateRecord, common FinalizeGuard, release handoff regression tests
- proposed snippet: `guard.require_fresh(head, plan_digest, terminal_queue_revision)`
- verify: missing/failed/stale/corrupt/pass, crash-at-write, direct bypass effect 0, secret canary 0, deploy authorization tests
- success: stale evidence로 PR/merge할 수 없고 final review는 계획당 1회다.
- stop: producer 없는 enforcement, keyword deploy authorization, secret-bearing evidence
- tradeoff: 대형 epoch/profile binding보다 단순하지만 병합 안전에 필요한 값은 잠근다.

### Phase 5: Build the source convergence manifest

- priority: P2
- target: latest `origin/main`, `b2db89e` ancestry, canonical `383ea76`
- work: 각 누적 commit/path를 `keep|supersede|drop|semantic-port`로 분류한다.
- code snippet: 필요 없음. read-only Git provenance phase다.
- verify: unrelated history 0, selected commit/path/source SHA table
- success: clean integration branch에 넣을 변경이 명확하다.
- stop: fetch 실패, dirty ownership 불명확, provenance 불명확
- tradeoff: 즉시 rebase보다 안전하며 누적 history 오염을 차단한다.

### Commit 6: Create the clean integration branch

- priority: P2
- target: latest `origin/main` 기반 새 branch와 Phase 5 manifest
- change: 필요한 code/test/docs만 cherry-pick 또는 semantic port하고 최신 main과 충돌을 해결한다.
- code snippet: Git integration unit이라 필요 없음.
- verify: clean status, selected diff audit, focused+full Codex Flow tests
- success: origin/main 대비 의도한 변경만 남는다.
- stop: unrelated diff, safety assertion 유실, canonical contract 충돌
- tradeoff: 한 번 더 옮기지만 PR surface와 rollback이 명확해진다.

### Phase 7: Fresh review, PR, CI and merge

- priority: P3
- target: clean integration branch exact HEAD
- work: `review-all-in-one` 구현 후 모드, canonical `테스트`, push·PR, same-HEAD CI, blocker/important 0일 때 merge
- code snippet: 해당 없음.
- verify: full tests, required CI, exact PR HEAD, merged main SHA
- success: intended-only PR이 main에 병합되고 merged SHA에서 재검증된다.
- stop: failing CI, stale HEAD, blocker/important, 권한 문제
- tradeoff: 속도보다 same-HEAD evidence를 우선한다.

### Phase 8: Shrink and semantically sync canonical implementation skill

- priority: P4
- target: canonical `구현커밋/SKILL.md`, new `구현커밋/references/runtime-adapter.md`, new `구현커밋/references/recovery.md`, related contract tests; `테스트/SKILL.md`는 read-only baseline
- work:
  - `구현커밋` 본문을 90~130줄 목표로 줄이고 세 핵심 책임만 남긴다.
  - runtime 상세는 `references/runtime-adapter.md`, recovery 상세는 `references/recovery.md`로 이동한다.
  - PR·merge·deploy·cleanup은 새로 복제하지 않고 canonical `테스트/SKILL.md` 링크와 ownership 문장만 남긴다.
  - main-first/review policy를 semantic merge하면서 기존 release handoff를 보존한다.
  - 별도 branch/commit/PR로 병합하며 mirror 전체 복사를 금지한다.
  - 현재 canonical 작업 경로의 unrelated dirty 파일을 피하기 위해 latest canonical `origin/main` 기반 별도 clean worktree에서만 편집한다.
  - PR 병합 뒤 설치 symlink가 가리키는 원본 working tree는 changed-path overlap이 0이고 fast-forward가 가능한 경우에만 `origin/main`으로 갱신한다. overlap 또는 non-FF이면 설치 경로를 그대로 보존하고 activation을 held 처리한다.
- code snippet: 문서·contract unit이라 필요 없음.
- verify: release contracts 5/5, full canonical tests, reference link validator, line-count target, clean-worktree diff, original dirty-path overlap 0, ff-only activation; required-contract checklist가 line target보다 우선함을 확인
- success: 짧은 본문이 runtime과 release ownership을 모순 없이 설명한다.
- stop: release assertion 감소, broken reference, unrelated dirty file 포함, original dirty-path overlap, non-FF activation, mirror overwrite, 또는 90~130줄을 맞추기 위해 필수 계약을 삭제해야 하는 경우
- tradeoff: reference 파일이 늘지만 기본 스킬 로딩과 이해 비용을 줄인다.

### Phase 9: Canary and acceptance metrics

- priority: P5
- target: merged runtime main과 canonical skills
- work: ordinary 3-unit fixture와 high-risk isolated fixture를 비교 실행한다.
- code snippet: 검증 phase라 필요 없음.
- verify: ordinary child 0, final reviewer 1 이하, high-risk isolation 유지, automatic retry 0, orphan 0
- success: orchestration overhead 15% 이하 또는 원인별 개선이 계측된다.
- stop: finding escape 증가, state loss, stale evidence reuse, deploy ownership 회귀
- tradeoff: external state/cache 최적화는 핵심 정책 효과를 확인한 뒤 진행한다.

## Acceptance Metrics

- ordinary 3-unit child implementer: `0`
- ordinary main cumulative review: final `1`
- same-unit automatic retry: `0`
- out-of-scope retry: `0`
- final gate 없는 PR/merge effect: `0`
- Codex Flow full suite: baseline `230/230` 이상
- canonical release contracts/full suite: `5/5`, `73/73` 이상
- final blocker/important: `0`
- evidence secret 원문: `0`
- `구현커밋/SKILL.md` 목표 길이: 90~130줄. 필수 계약 보존이 우선이며 줄 수를 맞추기 위한 assertion 삭제는 금지

## Plan Quality Check

- Alternative considered: child 완전 삭제, central coordinator 유지, child-per-unit 미세 최적화.
- Why this plan: mandatory child/full review/automatic repair라는 1차 원인을 제거하면서 4A isolation은 필요한 경우에만 보존한다.
- Tradeoff: main bias가 커질 수 있어 final cumulative review와 high-risk `per_unit`을 안전망으로 둔다.
- What this plan may still miss: main handoff UX, integration conflict 규모, canary latency 분포.
- When to stop and revise: scope/exact-commit bypass, final gate bypass, release handoff 약화, 줄 수 목표 때문에 필수 계약이 누락될 때.

## 해결전략검토 반영

- 판정: `조건부 적절 → 반영 후 적절` (`High` confidence)
- 가장 강한 반대 근거:
  - 현재 실행이 high-risk classifier에 의해 child로 되돌아가면 main-only 목표가 시작부터 깨진다.
  - canonical dirty working tree를 직접 갱신하면 사용자 변경을 침범하거나 설치본 activation이 모호해진다.
- 반영:
  - 이번 plan 실행에만 main-only/final-only/no-delegation override를 명시했다.
  - canonical skill은 별도 clean worktree에서 PR하고, 설치 경로는 overlap 0 + ff-only일 때만 활성화한다.
- Kill Criteria: silent fallback, automatic retry, assertion 약화, secret-bearing evidence, destructive cleanup은 모두 금지된 상태다.
- 판정 변경 조건: main transaction이 scope/HEAD/CAS를 우회하거나 canonical activation이 dirty overlap을 무시하면 다시 `부적절`로 내린다.

## Operator 결정 필요 사항

- 상태: 없음
- 기본값: main-first, `final_only`, automatic repair off, high-risk/명시 요청만 isolated `per_unit`, clean integration branch, semantic skill sync
- 이유: 사용자가 이 방향을 최상위 목표로 지정했다.

## 검토용 결과물

- plan: 이 문서
- request artifact: `../../request-refiner-artifacts/2026-07-21-main-first-codex-flow-convergence-refined-request.md`
- research: `research.md`와 latency audit
- HTML: 생략
- test link: localhost/deploy URL 해당 없음. baseline tests와 후속 phase 명령이 대체 검증이다.

## HTML 생략 보고서

- 판정: 생략 가능
- 사유: CLI/backend orchestration과 Git/skill contract 계획이며 시각 UI 변경이 아니다.
- 대체 검토물: 이 계획의 priority map, conflict matrix, disposition table과 acceptance metrics.

## 구현 후 검토 리스트

- 4A isolation/non-mutation과 exact-path/dirty/source-drift 회귀
- main-first policy, review matrix, recovery taxonomy, final bypass
- wrong-HEAD/manual adoption, heartbeat/redaction 보존
- main transaction replay/owner conflict, final-gate atomic crash와 corruption 거부
- child deploy 금지와 main release finalization
- clean integration diff, same-HEAD CI, canonical semantic sync

## Validation And Next Gate

- plan heading, manifest, target, change, verification, success, stop, snippet, tradeoff를 확인한다.
- 구현 전 `review-all-in-one`과 `해결전략검토`로 이 문서를 읽기 전용 검토한다.
- blocker 또는 방향 변경 finding이 없을 때만 메인 에이전트가 Commit 4B부터 순차 실행한다.
- 병렬 run-all과 child-per-unit 구현은 사용하지 않는다.

## 후행 실행

후행 실행: 메인 에이전트가 `구현커밋`의 transaction contract만 사용해 Commit 4B → 4C → 4D → Phase 5 → Commit 6 → Phase 7 → Phase 8 → Phase 9 순서로 진행한다. child-per-unit 실행과 병렬 `run-all`은 비활성화한다. 이번 계획 작성 단계에서는 코드 구현, 새 PR, merge, deploy와 canonical skill sync를 실행하지 않는다.

## 실행 진행 상황 (2026-07-21)

- Commit 4B 완료: `0089580` — main-owned unit transaction과 ordinary child 0 계약.
- Commit 4C 완료: `dfad52c` — `final_only` 기본값과 명시적/high-risk `per_unit`.
- Commit 4D 완료: `27e9069` — atomic, same-HEAD, fail-closed finalization gate.
- Phase 5/Commit 6 완료: `4de1f43` — latest `origin/main`과 선형 정본 수렴 및 integration provenance.
- Phase 7 누적 검토: blocker 0, important 0, minor 1. 상세는 `final-review.md`.
- Phase 7 pre-evidence 테스트: full `242/242`, focused `81/81`. 상세는 `final-test-report.md`.
- 다음 gate: evidence commit 후 exact final HEAD 전체 테스트 → final gate → push/Draft PR → same-HEAD CI → merge.
- Product deploy: 이번 계획의 승인 범위 밖이므로 실행하지 않는다.
