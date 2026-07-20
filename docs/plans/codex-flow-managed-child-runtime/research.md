# Research

## Goal

`run-next`/`run-all`이 호출자에게 `CODEX_FLOW_CHILD_HOME`과 `CODEX_FLOW_CHILD_MANIFEST` 사전 주입을 요구하는 반복 실패의 causal chain을 확인하고, 기존 exact-closure attestation을 약화하지 않으면서 raw canonical CLI가 스스로 실행 가능한 child runtime을 준비하는 설계 근거를 확정한다.

선택 모드: `Pre-Plan Research Gate`.

## Scope And Entry Points

- canonical entrypoint: `scripts/codex_flow.py` → `codex_flow.cli` → `runner.run_next`/`runner.execute_unit`
- child execution boundary: `codex_flow/runner.py::execute_unit`
- isolation, discovery, attestation: `codex_flow/codex_cli.py`
- user wrapper: `/Users/moonsoo/projects/codex-skills-user/scripts/implementation_commit.py`
- precedent: `/Users/moonsoo/projects/development-workflow/plugins/chronica-workflow/skills/implementation-commit/scripts/prepared_child.py`
- regression surface: `tests/test_codex_cli.py`, `tests/test_child_attestation.py`, implementer agent argument plumbing, runtime docs

웹/커뮤니티 조사는 수행하지 않았다. 이 문제는 현재 checkout의 내부 실행 계약과 로컬 Codex CLI `0.144.1` 동작이 정본이므로 external 10-platform/20-evidence bar는 `threshold intentionally narrowed`로 처리한다.

## Relevant Files

- `codex_flow/codex_cli.py`
- `codex_flow/runner.py`
- `codex_flow/implementer_agent.py`
- `codex_flow/cli.py`
- `tests/test_codex_cli.py`
- `tests/test_child_attestation.py`
- `README.md`
- `skills/구현커밋/SKILL.md`
- `.codex-flow/plans/2026-07-16-codex-flow-fail-closed-child-runtime-plan/source-plan.md`
- `.codex-flow/plans/2026-07-16-codex-flow-loaded-skill-closure-repair-plan/source-plan.md`
- `.codex-flow/plans/2026-07-16-codex-flow-plugin-cache-closure-repair-plan/source-plan.md`
- `/Users/moonsoo/projects/codex-skills-user/scripts/implementation_commit.py`
- `/Users/moonsoo/projects/codex-skills-user/scripts/codex_flow_entrypoint.py`
- `/Users/moonsoo/projects/development-workflow/plugins/chronica-workflow/skills/implementation-commit/scripts/prepared_child.py`
- `/Users/moonsoo/projects/development-workflow/plugins/chronica-workflow/skills/implementation-commit/scripts/implementation_commit.py`

## Current Behavior

### Confirmed facts

1. 사용자-facing wrapper는 canonical entrypoint를 찾아 `execv`할 뿐 child 준비를 하지 않는다. 따라서 wrapper 경로와 raw `scripts/codex_flow.py` 경로 모두 같은 사전조건을 만난다.
2. `runner.execute_unit`은 unit의 required skill closure를 계산한 직후 `generate_child_attestation()`을 호출한다.
3. `generate_child_attestation()`은 `CODEX_FLOW_CHILD_HOME` 또는 manifest가 없으면 즉시 실패한다. 이 실패는 `child_runtime_environment()`가 가진 기본 repo-hash home 생성 로직보다 먼저 발생한다.
4. `verify_child_attestation()`과 runner의 manifest lookup도 process-global 환경변수에 다시 의존한다.
5. 결과적으로 기본 home 생성 로직과 문서상 “기본 child 자동 사용” 계약은 실제 실행에서 연결되지 않는다.
6. 현재 테스트는 이 모순을 안전장치로 고정해, 환경변수가 없으면 implementer를 시작하지 않고 Git oracle이 바뀌지 않는다고 assertion한다. 새 동작은 “미준비 상태를 허용”하는 것이 아니라 “먼저 안전하게 준비하고 같은 no-edit gate를 통과”하도록 이 테스트 계약을 교체해야 한다.

### Repeated observation

- 실제 랜딩페이지 구현커밋에서 raw command가 `prepared child home is required in CODEX_FLOW_CHILD_HOME`로 `needs_work`가 됐다.
- 2026-07-16 fail-closed/closure repair 계획들도 prepared runtime을 caller/bootstrap prerequisite로 두었고, 후속 실행은 매번 수동 환경변수 주입으로만 재개됐다.
- 머신의 repo-hash 기본 child homes 다수는 `config.toml`만 있고 exact manifest/skill closure가 없어, “home 생성”과 “prepared runtime 생성”이 별개로 남아 있음을 보여준다.

## Data Flow And Control Flow

현재 흐름:

```text
raw run-next
  → runner.execute_unit
  → required_review_skills(unit manifest)
  → generate_child_attestation
  → env pair missing: needs_work
  ✕ child_runtime_environment default home
  ✕ implementer
```

권장 흐름:

```text
raw run-next
  → runner.execute_unit
  → exact required closure 계산
  → ensure_prepared_child_runtime(required closure)
      ├─ explicit env pair: validate/reuse
      └─ no pair: resolve → private stage → manifest → fresh inventory → atomic publish/reuse
  → generate_child_attestation(child_home, manifest)
  → verify_child_attestation(child_home, manifest)
  → same prepared child passed explicitly to implementation + resumed review
  → implementer launch
```

실패는 계속 `ensure`/attestation 구간에서 `needs_work`, `changed_paths=[]`로 끝나야 한다.

## Existing Abstractions And Boundaries

- `child_runtime_environment()` owns safe config/auth/trust inheritance, private permissions, repo/parent-home separation.
- `load_child_closure_manifest()` owns exact manifest schema, contained path, tree digest, ownership-class, closure validation.
- app-server `initialize` + `skills/list` owns fresh child inventory; model self-report는 이미 폐기됐다.
- `generate_child_attestation()`/`verify_child_attestation()` own plan/runtime/plugin/CLI/discovery/TTL/nonce binding.
- `runner.execute_unit` is the shared last pre-edit boundary for raw CLI and programmatic execution.
- `CodexImplementerAgent` owns implementation and same-session review child launches; prepared home must be explicitly threaded here so attested runtime과 실행 runtime이 같아야 한다.
- explicit profile/custom-provider는 현재 prepared override 전용 fail-closed 계약이다.

## Side Effects And Integration Points

- managed runtime write는 target repo가 아니라 `${CODEX_CHILD_RUNTIME_ROOT:-$CODEX_HOME/child-runtimes}/codex-flow/` 아래에서만 발생한다.
- parent `auth.json`은 기존 계약대로 child에서 symlink 접근한다. 이는 인증 파일 복제가 아니라 필요한 credential access이며, unrelated secret/config 복사는 금지한다.
- source skill tree는 parent `CODEX_HOME/skills/<id>`의 canonical target을 private staging에 복제한다.
- parent plugin/MCP/hook config는 복사하지 않는다. ambient namespaced skills가 Codex runtime에 의해 별도 주입되면 exact inventory의 external class로만 기록하고, required custom skill source 대신 사용하지 않는다.
- cache publish는 incomplete staging이 reader에게 보이지 않도록 lock + atomic rename을 사용해야 한다.
- runner log에는 `managed_child_runtime_prepare|reuse|denied`, repo/closure fingerprint, required ids, manifest digest만 기록하고 token/auth/config 원문은 기록하지 않는다.

## Risk To Surrounding Systems

- process-global `os.environ`을 임시 변경하면 concurrent plan/unit이 다른 child를 사용할 수 있다. home/manifest는 함수 인자와 agent state로 명시 전달해야 한다.
- repo-only cache key는 서로 다른 unit closure가 같은 directory를 교체하게 해 active child를 손상시킬 수 있다. repo + required/resolved ids + source digests + sanitized config + CLI/schema version fingerprint가 필요하다.
- source symlink를 그대로 복사하거나 내부 symlink를 따라가면 target repo 밖의 불필요한 데이터가 child에 유입될 수 있다. source root는 resolve하되 내부 symlink는 fail-closed로 거부한다.
- parent inventory의 모든 personal skill을 external allowlist로 넣으면 exact closure가 의미를 잃는다. custom standalone skills는 required set만 copy하고, non-required personal skill은 제외한다.
- ambient plugin/superpowers는 현재 Codex CLI가 child home 밖에서 주입할 수 있다. unexpected id는 최종 fresh discovery의 exact set mismatch로 막아야 하며, 해당 external set은 cache fingerprint와 manifest에 포함해야 한다.
- 기존 explicit override 사용자에게 자동 교체를 적용하면 custom provider/profile 계약을 깰 수 있다. env pair가 있으면 managed builder는 write하지 않고 validation만 수행한다.

## Do Not Duplicate Or Bypass

- manifest tree hashing/validation을 새 module에서 다시 구현하지 말고 `path_tree_hash()`와 `load_child_closure_manifest()`를 재사용한다.
- fresh inventory를 모델 prompt/self-report로 되돌리지 않는다.
- wrapper에만 preflight를 추가해 raw CLI/programmatic runner를 우회 가능하게 만들지 않는다.
- isolation을 끄거나 missing manifest를 빈 closure로 간주하지 않는다.
- `run_codex_exec()`가 다시 process-global env에서 다른 home을 선택하게 두지 않는다.
- Chronica prepared-child builder의 plugin-specific 24-skill 고정 closure를 canonical core로 복사하지 않는다. staging/validation/atomic publish 원칙만 재사용한다.

## Open Questions

- namespaced plugin required skill의 canonical payload 설치/해시 정책은 현재 standalone skill copy보다 복잡하다. 이번 managed path는 현재 Codex inventory에서 exact namespaced id가 실제로 활성화된 경우 external ownership으로 attestation하며, 활성화되지 않았거나 short id가 모호하면 fail-closed로 남기는 것이 가장 작은 호환 경계다.
- ambient external path까지 tree-hash하는 manifest v2는 보안을 강화하지만 현재 오류의 최소 root fix보다 큰 schema migration이다. 이번 변경은 기존 v1 exact-id semantics를 보존하고 후속 hardening 후보로 기록한다.
- explicit profile/custom-provider는 기존 증거에 따라 계속 explicit prepared pair를 요구한다.

## Solution Options

### Option A — Core managed-runtime manager at `runner.execute_unit` (recommended)

- operating principle: finalized required closure 직후, pre-edit shared boundary에서 prepare/validate를 수행하고 결과를 명시 인자로 attestation과 implementer에 전달한다.
- supporting evidence: runner가 모든 실제 unit 실행의 공통 boundary이며, 기존 attestation/no-edit oracle과 겹친다. Chronica precedent가 private staging, manifest validation, fresh discovery, atomic replacement의 실현 가능성을 증명한다.
- fit conditions: raw CLI, wrapper, programmatic runner, run-all이 동일 계약을 사용해야 할 때.
- failure modes: source ambiguity, symlink, unsafe path, inventory mismatch, lock/publish 실패는 `needs_work` before edit.
- implementation implication: 새 `codex_flow/child_runtime.py`, explicit runtime parameter plumbing, focused concurrency/cache/negative tests, docs contract alignment.

### Option B — Auto-provision inside `generate_child_attestation`

- operating principle: missing env를 attestation generator가 직접 보완한다.
- supporting evidence: call-site 변경은 작다.
- fit conditions: attestation callers가 모두 stateful preparation을 기대할 때.
- failure modes: evidence generation과 mutable provisioning 책임이 섞이고 verifier/implementer의 global env 의존이 남는다. unit closure와 cache key 입력도 generator에 새로 섞인다.
- implementation implication: 단기 패치는 가능하지만 곧 별도 manager 분리가 필요하다.

### Option C — Wrapper/CLI preflight only

- operating principle: `implementation_commit.py` 또는 CLI dispatch 전에 env를 채운다.
- supporting evidence: Chronica wrapper가 이 방식으로 작동한다.
- fit conditions: 단일 packaging wrapper만 지원할 때.
- failure modes: raw `scripts/codex_flow.py`, alternate entrypoint, programmatic runner가 계속 실패한다. canonical bug가 wrapper로 이동한다.
- implementation implication: core manager 위의 compatibility adapter로만 허용 가능하다.

### Option D — Route-time provisioning persisted in execution context

- operating principle: route 시점에 child를 만들고 plan metadata에 고정한다.
- supporting evidence: execution worktree metadata와 plan binding이 이미 존재한다.
- fit conditions: 모든 required closure가 route 시 확정되고 이후 source/config가 변하지 않을 때.
- failure modes: status/route가 비싼 side effect를 갖고, 기존 routed plan migration과 unit별 closure invalidation이 필요하며 direct runner는 여전히 별도 처리해야 한다.
- implementation implication: 실행-time manager가 안정된 뒤 cache warm-up optimization으로만 고려한다.

## Plan Implications

- Option A를 채택한다.
- Commit 1은 core preparation manager와 deterministic resolver/cache/publish를 구현한다.
- Commit 2는 runner → attestation → implementer/review 전체에 prepared runtime을 명시 전달하고 missing-env regression을 self-preparation success regression으로 바꾼다.
- Commit 3은 docs/diagnostics 및 raw no-env CLI/integration 증거를 고정한다.
- 초기 bootstrap에서 기존 explicit prepared pair 사용은 self-preparation 호출부가 연결되는 Commit 2까지로 한정한다. Commit 3과 최종 smoke는 env pair 없는 raw command로 실행해야 한다.
- HTML은 CLI/backend runtime 수정이라 생략하고, pytest + disposable CLI log를 검토 표면으로 쓴다.

## Source Evaluation

| Source | Claim used | Evidence lanes | Score | Decision | Local transfer | Do not copy |
| --- | --- | --- | --- | --- | --- | --- |
| current Codex Flow source/tests | causal chain, exact attestation, no-edit boundary | official local source, empirical tests, local fit | A | Adopt | direct target | missing-env failure assertion itself |
| current Codex CLI `0.144.1` help/app-server behavior | machine-readable inventory surface | installed primary runtime, empirical | A | Adopt | same executable used by child | model-authored inventory |
| Chronica `prepared_child.py` | private staging, validation, atomic replacement precedent | adjacent repository source, empirical precedent | B | Pilot selectively | reuse mechanisms only | fixed 24-skill closure, caller-only ownership, unlocked `.previous` race |
| machine prepared homes/manifests | actual v1 shape and recurring manual preparation evidence | empirical local artifacts | B | Use as diagnostic evidence | informs compatibility | hard-coded home names or stale payloads |

Strongest evidence: current source plus focused no-edit tests. Weakest adopted evidence: adjacent Chronica provisioner; only generic staging principles transfer. Rejected: wrapper-only fix, isolation opt-out, empty-manifest fallback.

## Evidence

- `codex_flow/codex_cli.py:552-576`: env pair hard requirement before default environment path.
- `codex_flow/codex_cli.py:607-687`: exact attestation binding and replay/TTL checks.
- `codex_flow/codex_cli.py:727-853`: default repo-hash home, safe config/auth/trust isolation.
- `codex_flow/runner.py:262-320`: required closure and pre-edit attestation boundary.
- `tests/test_child_attestation.py:601-626`: current missing-env no-launch/no-edit oracle.
- `tests/test_codex_cli.py:82-143`: private config filtering and repo namespace reuse.
- `skills/구현커밋/SKILL.md:36-44`: documented default child behavior.
- `/Users/moonsoo/projects/codex-skills-user/scripts/codex_flow_entrypoint.py:43-49`: wrapper only execs canonical entrypoint.
- `/Users/moonsoo/projects/development-workflow/plugins/chronica-workflow/skills/implementation-commit/scripts/prepared_child.py:159-267`: staging/provision/discovery/publish precedent.
- command, 2026-07-19: `codex --version` → `codex-cli 0.144.1`.
- command: `rg -n "child_runtime|CHILD_HOME|CHILD_MANIFEST|attestation|prepared|manifest|closure" codex_flow tests README.md skills/구현커밋/SKILL.md`.
- independent researcher: `child-runtime-precedent-research-20260719`, read-only, completed; main agent re-read cited source before synthesis.
