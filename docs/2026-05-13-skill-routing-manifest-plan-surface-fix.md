# 2026-05-13 | Skill Routing Manifest plan surface fix

## Problem

Codex Flow queue state can already carry `required_skills` and `optional_skills` per unit, but a human-readable `## Skill Routing Manifest` can still be missing from `plan.md` after planner output. That creates a partial working state: machines know the routing, but fresh Codex sessions and reviewers cannot see it in the plan surface.

## Goal

Make `plan.md` and `queue.json` converge on the same skill-routing contract:

- New plans include `## Skill Routing Manifest`.
- If a planner agent rewrites `plan.md` without the section, Codex Flow restores it from queue state.
- Queue sync continues to read skill routing from the manifest when the human surface is authoritative.

## Scope

- `codex_flow/plans.py`
  - Add a post-planner guard that ensures `plan.md` contains `## Skill Routing Manifest`.
  - Reuse the existing manifest renderer so generated and repaired sections have the same shape.
- `tests/test_plans.py`
  - Add a regression test for a planner that overwrites `plan.md` without the manifest.

## Out Of Scope

- No changes to the standalone HTML/PDF design-system project.
- No changes to generated user plan content outside the missing manifest insertion point.
- No git branch, PR, or merge automation.

## 검토용 결과물

- 상태: verified
- 대체 검토물:
  - `pytest tests/test_plans.py tests/test_plan_readiness.py tests/test_agent_roles.py tests/test_runner_brief.py`
  - Code inspection of the generated `plan.md` repair path.

## HTML 생략 보고서

- 판정: 생략 가능
- 생략 사유:
  - This is a CLI and Markdown generation bug. The user-facing artifact is `plan.md`, not a browser UI.
- 대체 검토물:
  - Unit tests that generate plans and assert the manifest is present.
- 사용자가 바로 열어볼 링크:
  - This plan file and the changed source/test files.

## Implementation Steps

1. Inspect current plan creation flow around `write_plan_files`, planner rewriting, and queue sync.
2. Add a helper that inserts `## Skill Routing Manifest` before `## Commit Units` when it is missing.
3. Call the helper after `selected_planner.write_plan(...)` and before `sync_queue_cache_from_plan(...)`.
4. Add a regression planner in `tests/test_plans.py` that intentionally writes a plan without the manifest.
5. Run the narrow relevant pytest slice.

## Success Criteria

- [x] A planner that drops `## Skill Routing Manifest` no longer leaves `plan.md` without the section.
- [x] The inserted manifest uses queue `required_skills`, `optional_skills`, and `skill_routing_evidence`.
- [x] Existing manifest parsing and queue sync tests still pass.

## Verification Result

- `python3 -m pytest tests/test_plans.py tests/test_plan_readiness.py tests/test_agent_roles.py tests/test_runner_brief.py`
  - Result: 23 passed
- `python3 -m pytest`
  - Result: 48 passed

## Commit Unit

- Single scoped patch: manifest surface repair guard plus regression test.
