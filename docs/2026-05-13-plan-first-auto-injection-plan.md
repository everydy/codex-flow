# plan-first 자동 주입 구현 계획

## 검토용 결과물

- 대상 저장소: `/Users/moonsoo/projects/codex-flow`
- 검토 대상: Codex Flow plan 생성, Skill Routing Manifest 보정, 실행 프롬프트 전달 경로
- 결과물 형태: CLI/tooling 동작과 pytest 회귀 테스트

## HTML 생략 보고서

이 작업은 브라우저 UI가 아니라 Codex Flow의 계획 생성/큐 동기화 로직을 바꾸는 CLI/tooling 변경이다. 검토 가능한 산출물은 Markdown plan, manifest 텍스트, queue cache, pytest 결과이므로 별도 HTML artifact는 만들지 않는다.

## 목표

Codex Flow가 구현 성격의 작업 단위를 만들거나 기존 plan manifest를 읽을 때 `plan-first-implementation`을 필요한 required skill로 자동 포함하게 한다. 단, 상태 확인, 리뷰, 브리핑, QA, test-only 같은 구현 계획이 불필요한 작업에는 자동 주입하지 않는다.

## Review Follow-up

- 상태: planned
- 근거: `review-all-in-one` 검토에서 기존 plan/run-next 경로 repair 누락 important finding과 manifest row header 판정 minor finding이 나왔다.
- 추가 목표:
  - 이미 존재하는 `plan.md`와 `queue.json`을 `run-next`, `run-all`, review/PR dry-run 같은 실행 경로에서 읽을 때도 `plan-first-implementation` repair가 먼저 적용되게 한다.
  - `Phase` 단어가 phase cell이 아닌 row 본문에 들어간 정상 manifest row를 header로 오인하지 않게 한다.

## 대상 파일

- `/Users/moonsoo/projects/codex-flow/codex_flow/plans.py`
- `/Users/moonsoo/projects/codex-flow/codex_flow/planner_agent.py`
- `/Users/moonsoo/projects/codex-flow/tests/test_agent_roles.py`
- `/Users/moonsoo/projects/codex-flow/tests/test_plans.py`
- `/Users/moonsoo/projects/codex-flow/tests/test_runner_brief.py`
- `/Users/moonsoo/projects/codex-flow/README.md`

## 함수와 API

- `codex_flow.plans.DEFAULT_UNITS`
- `codex_flow.plans.render_skill_routing_manifest`
- `codex_flow.plans.ensure_plan_skill_routing_manifest`
- 신규 helper: implementation-like unit 판정, required skill 보정, 기존 manifest row 보정
- `codex_flow.planner_agent.build_planner_prompt`

## 변경 순서

1. `plans.py`에 `plan-first-implementation` 자동 주입 helper를 추가한다.
2. 기본 unit과 manifest 렌더링 경로에 helper를 적용한다.
3. 플래너가 이미 작성한 `## Skill Routing Manifest` 섹션도 큐 동기화 전에 보정한다.
4. planner prompt에 조건부 자동 주입 정책을 명시한다.
5. README의 manifest 예시를 최신 정책에 맞춘다.
6. pytest 회귀 테스트를 갱신하고 신규 케이스를 추가한다.

### Review Follow-up 변경 순서

1. `plans.load_queue()`가 기존 `queue.json`을 반환하기 전 `plan.md` manifest repair를 수행하게 한다.
2. repair 뒤 `plan_readiness.sync_queue_cache_from_plan()`으로 queue cache를 갱신해 `run-next` prompt 경로까지 같은 required skills를 받게 한다.
3. `repair_plan_first_manifest_row()`의 header skip 조건을 cell 기반으로 좁힌다.
4. 기존 plan + queue cache + 빠진 `plan-first-implementation` 상태에서 `runner.run_next()` prompt가 보정되는 테스트를 추가한다.
5. `Phase` 문자열이 정상 row phase에 포함되어도 보정되는 테스트를 추가한다.

## 유지할 동작

- `review-all-in-one`, `qa-gate` final gate 라우팅은 유지한다.
- `mission-completion-harness` 같은 기존 required skill은 삭제하지 않는다.
- 이미 `plan-first-implementation`이 있는 row에는 중복 삽입하지 않는다.
- 리뷰/QA/상태/브리핑 성격의 row에는 자동 삽입하지 않는다.
- `run-next` implementer prompt는 Skill Routing Manifest의 required skills를 그대로 보여준다.
- 기존 plan을 읽는 실행 경로에서도 새 plan 생성 경로와 같은 manifest 보정 결과를 유지한다.

## 검증 명령

```bash
PYTHONPATH=. pytest -q tests/test_agent_roles.py tests/test_plans.py tests/test_runner_brief.py tests/test_plan_readiness.py
```

## 성공 기준

- 기본 생성 plan의 구현 관련 commit unit required skills에 `plan-first-implementation`이 포함된다.
- 플래너가 빠뜨린 기존 manifest도 저장된 plan과 queue cache에서 보정된다.
- review/QA-only row에는 `plan-first-implementation`이 자동 삽입되지 않는다.
- implementer prompt에 보정된 required skills가 노출된다.
- 이미 존재하는 plan/queue를 `runner.run_next()`로 읽어도 prompt에 보정된 required skills가 노출된다.
- phase text에 `Phase` 단어가 들어간 정상 row도 header로 오인하지 않는다.
- 위 pytest 명령이 통과한다.

## 커밋 단위

- 단일 커밋 권장: `Inject plan-first routing for implementation units`
- 커밋 완료: `7aed61d Inject plan-first routing for implementation units`

## 중단 조건

- 기존 manifest 파서가 row를 안정적으로 보정할 수 없어서 plan 내용 손상 위험이 생기면 중단한다.
- 자동 주입 판정이 상태/리뷰/QA 작업까지 넓게 오탐하면 중단하고 정책을 좁힌다.
- pytest가 unrelated dirty worktree나 환경 문제로 실패하면 실패 원인을 분리해 보고한다.
