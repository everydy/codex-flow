# 2026-07-16 | App-Server Child Skill Attestation Repair Plan

## Goal

Replace model self-report based child skill discovery with the Codex app-server `skills/list` protocol so a prepared isolated child can prove the exact enabled skill closure before any edit.

This is the minimum self-hosting bootstrap needed before the approved Chronica Workflow Skills migration can continue through normal `구현커밋` execution.

## Scope

- Repository: `/Users/moonsoo/projects/codex-flow`
- Runtime source: `codex_flow/codex_cli.py`
- Tests: `tests/test_child_attestation.py`
- Preserve: exact child-home, manifest, plugin/runtime digest, plan digest, CLI version, TTL, nonce replay, and no-edit failure gates.
- Exclude: Chronica naming, plugin packaging, remote repository rename, Release 2 orchestrator, deploy, and publish.

## Skill Routing Manifest

| Phase | Required skills | Optional skills | Evidence |
| --- | --- | --- | --- |
| Commit 1: Replace model self-report with app-server inventory | `systematic-debugging`, `test-driven-development`, `review-all-in-one` | `qa-gate` | Reproduced isolated-child false negative, app-server `initialize` and `skills/list` contract, focused pytest |
| Final Gate | `review-all-in-one`, `qa-gate` | `테스트` | Full pytest, CLI help smoke, diff check, no-edit regression |

## Implementation Plan

### Commit 1: Replace model self-report with app-server inventory

- Target files:
  - `codex_flow/codex_cli.py`
  - `tests/test_child_attestation.py`
- Change:
  - Start `codex app-server --listen stdio://` inside the exact prepared `CODEX_HOME`.
  - Verify `initialize.result.codexHome` equals the prepared child home.
  - Request `skills/list` for the execution repository with `forceReload: true`.
  - Accept only enabled skills from the matching cwd entry.
  - Bind raw child skills to declared `skills/` roots, plugin skills to declared plugin payload roots, and external skills to their explicit allowlist.
  - Preserve exact-set comparison and all existing attestation bindings.
- Proposed protocol shape:

  ```json
  {"id":2,"method":"skills/list","params":{"cwds":["<execution-repo>"],"forceReload":true}}
  ```

- Verification:
  - RED: `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest tests/test_child_attestation.py -q`
  - GREEN: rerun the focused test file and then the full suite.
  - `python3 scripts/codex_flow.py --help`
  - `git diff --check`
- Success criteria:
  - A fake app-server inventory passes without any model discovery prompt.
  - wrong child home, missing/extra skills, disabled skills, wrong cwd, or owner/path mismatch fails before edit.
  - existing plan/runtime/plugin/TTL/replay bindings remain covered.
- Stop conditions:
  - the current Codex app-server response differs from the locally observed `skills/list` schema;
  - proving plugin ownership would require trusting a model-authored value;
  - any test permits edits before the exact inventory check passes.

## Plan Quality Check

- Alternative considered: keep the model prompt and make it explicitly invoke each skill. Rejected because invocation changes the state being measured and still trusts model-authored inventory.
- Why this plan: app-server is the already passing machine-readable discovery surface used by the plugin conformance probe.
- Tradeoff: the runtime adds a bounded protocol client, but removes a false-negative and spoofable model self-report boundary.
- What this plan may still miss: future app-server schema changes; failures must remain fail-closed with a concrete protocol error.
- When to stop and revise: if the current CLI cannot return `initialize.codexHome` and a cwd-scoped `skills/list` response in the prepared child.

## Operator 결정 필요 사항

없음. 적용한 기본값: 현재 승인된 Chronica migration spec의 app-server 방향을 그대로 사용한다.

## 구현 후 검토 리스트

- 회귀 확인: required skill miss, exact closure mismatch, TTL, replay, digest and child path tests.
- 검증 확인: focused and full pytest, CLI help, diff check.
- 리뷰 관점: subprocess cleanup, protocol timeout, cwd/path ownership, no-edit boundary.
- Operator 재확인: 없음. 이 커밋은 제품 이름을 바꾸지 않는 공통 실행기 복구다.

## 검토용 결과물

- 이 계획 MD와 pytest 로그를 사용한다.
- HTML 생략 사유: CLI/backend attestation repair이며 시각적 검토 표면이 없다.

## 후행 실행

부트스트랩 커밋 통과 후 나머지 Chronica migration은 `구현커밋`으로 실행한다.
