# 2026-06-03 | Rename Codex Flow To 구현커밋 | implementation-commit-name-migration

## Scope

Codex Flow has changed from a broad "flow" router into a plan-first implementation runner: it adopts a plan-first Markdown source, executes Commit/Phase units, runs review gates, repairs blocker/important findings, and commits passing units.

This plan migrates the user-facing skill name to `구현커밋` while keeping `codex-flow` and `코덱스플로우` as compatibility aliases. The migration should make the new name canonical without breaking old commands, docs, wrappers, or active plans.

## Review Context

### review-swarm findings

- **Important: skill metadata cannot be migrated by directory symlink alone.**
  - `SKILL.md` files carry `name:` metadata. If `구현커밋/` simply symlinks to `codex-flow/SKILL.md`, the skill may still identify as `codex-flow`.
  - Safer plan: create a real `구현커밋/SKILL.md` as canonical, then make `codex-flow/SKILL.md` and `코덱스플로우/SKILL.md` wrapper aliases.
- **Important: runtime package and state directory should not be renamed in the first migration.**
  - Internal package `codex_flow`, script `scripts/codex_flow.py`, and state dir `.codex-flow/` are embedded in code, tests, docs, and existing local repos.
  - Renaming these now would increase breakage without changing user-facing behavior.
- **Important: script aliases should exist.**
  - Existing wrapper: `/Users/moonsoo/projects/codex-skills-user/scripts/codex_flow.py`
  - Runtime entrypoint: `/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py`
  - Add `implementation_commit.py` and Korean `구현커밋.py` wrappers or symlinks where practical.
- **Minor: historical plan docs can keep old names.**
  - Old docs under `docs/plans/` explain previous design decisions. Updating every historical mention would add noise and risk.
  - Active README, skill docs, agents metadata, wrapper docs, and tests should use `구현커밋` as the canonical name.

### review-all-in-one summary

The migration should be presented to users as:

> `구현커밋` is the canonical skill for plan-first implementation, review, repair, and commit. `코덱스플로우` and `codex-flow` remain aliases for backward compatibility.

This avoids a hard cutover and lets old habits keep working.

## Current Name Surface Inventory

Commit 1 freezes the current naming surface before any canonical skill or wrapper change. Evidence was collected with:

- `rg -n "codex-flow|코덱스플로우|Codex Flow|구현커밋|implementation-commit" README.md codex_flow scripts tests docs .codex-flow`
- `rg -n "codex-flow|코덱스플로우|Codex Flow|구현커밋|implementation-commit" codex-flow 코덱스플로우 scripts README.md` in `/Users/moonsoo/projects/codex-skills-user`
- `find . -maxdepth 3 \( -name '*codex*flow*' -o -name '*구현커밋*' -o -name '*implementation*commit*' -o -name '*코덱스플로우*' \) -print`
- `find /Users/moonsoo/projects/codex-skills-user -maxdepth 3 \( -name '*codex*flow*' -o -name '*구현커밋*' -o -name '*implementation*commit*' -o -name '*코덱스플로우*' \) -print`

### User-facing migration targets

- `README.md`
  - Current title and product copy lead with `Codex Flow`.
  - Korean shortcut section exposes `$코덱스플로우` commands.
  - Install/example commands clone and enter `codex-flow`.
  - This should later lead with `구현커밋`, while keeping `Codex Flow`, `$코덱스플로우`, and `codex-flow` as compatibility aliases.
- `skills/codex-flow/SKILL.md`
  - Current metadata `name:` is `codex-flow`.
  - Current visible heading is `Codex Flow`.
  - This must not become the canonical `구현커밋` skill by symlink alone because `name:` would remain wrong.
- `skills/코덱스플로우/SKILL.md`
  - Current metadata `name:` is `코덱스플로우`.
  - Current wrapper says it is the Korean alias for `codex-flow`.
  - Later it should point to canonical `구현커밋` instead.
- `/Users/moonsoo/projects/codex-skills-user/codex-flow/SKILL.md`
  - Current metadata `name:` is `codex-flow`.
  - Current `When To Use` includes `$codex-flow`, `Codex Flow`, and `$코덱스플로우`.
  - Later this should be an alias wrapper, not the canonical body.
- `/Users/moonsoo/projects/codex-skills-user/코덱스플로우/SKILL.md`
  - Current metadata `name:` is `코덱스플로우`.
  - It explicitly loads `/Users/moonsoo/projects/codex-skills-user/codex-flow/SKILL.md`.
  - Later it should load canonical `/Users/moonsoo/projects/codex-skills-user/구현커밋/SKILL.md`.
- `/Users/moonsoo/projects/codex-skills-user/codex-flow/agents/openai.yaml`
  - Current `display_name` is `Codex Flow`.
  - Current `default_prompt` starts with `$codex-flow`.
  - Later it should present `구현커밋` first and keep old aliases in prompt/help text.

### CLI and wrapper surfaces

- Runtime entrypoint remains `/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py`.
- Compatibility wrapper remains `/Users/moonsoo/projects/codex-skills-user/scripts/codex_flow.py`.
- New wrapper paths are not present yet:
  - `/Users/moonsoo/projects/codex-flow/scripts/implementation_commit.py`
  - `/Users/moonsoo/projects/codex-flow/scripts/구현커밋.py`
  - `/Users/moonsoo/projects/codex-skills-user/scripts/implementation_commit.py`
  - `/Users/moonsoo/projects/codex-skills-user/scripts/구현커밋.py`
- Commit 3 should add wrappers or symlinks that delegate to the existing runtime without requiring package installation.

### Intentional compatibility and internal surfaces

These surfaces should remain unless a later explicit migration expands scope:

- Internal Python package: `codex_flow`
- Local state directory: `.codex-flow`
- Runtime script: `scripts/codex_flow.py`
- Historical docs under dated plan files, including older `docs/plans/*codex-flow*` and `/Users/moonsoo/projects/codex-skills-user/docs/2026-05-11-*codex-flow*` plans
- Existing queue, source snapshot, ticket, and prompt paths under `.codex-flow/`
- Tests that assert internal state paths such as `.codex-flow`

### Alias and rename risks fixed by this inventory

- `구현커밋/` must be a real canonical skill directory with `name: 구현커밋`; do not rely on a directory symlink to `codex-flow`.
- `codex-flow` and `코덱스플로우` should become explicit alias wrappers so skill discovery and human-readable metadata do not conflict.
- Do not rename `codex_flow`, `.codex-flow`, or `scripts/codex_flow.py` in this migration. Those names are runtime compatibility surfaces, not the user-facing canonical brand.
- Keep historical docs classified as historical. Updating them all would obscure useful migration context and increase unrelated diff size.
- Remaining old-name hits after later commits must be classified as one of: compatibility alias, internal runtime/state, or historical document.

## 검토용 결과물

- 계획 MD: this file
- 테스트 링크:
  - Localhost: 해당 없음. Skill/runtime naming and wrapper migration.
  - Deploy: 해당 없음. Local CLI and skill metadata verification only.
- 상태: planned
- 실제 동작:
  - New user-facing skill name and aliases will be tested through file discovery, CLI help, and pytest.
- Mock:
  - 없음

## HTML 생략 보고서

- 판정: 생략 가능
- 생략 사유:
  - CLI/skill naming migration. No UI surface is involved.
- 대체 검토물:
  - Skill files, wrapper scripts/symlinks, `rg` checks, and pytest.
- 테스트 링크:
  - Localhost: 해당 없음.
  - Deploy: 해당 없음.
- 사용자가 바로 열어볼 링크:
  - this plan file

## Operator 결정 필요 사항

- 상태: 없음
- Codex가 선택한 기본값:
  - Canonical user-facing name: `구현커밋`
  - English internal/CLI alias: `implementation-commit`
  - Compatibility aliases: `codex-flow`, `코덱스플로우`, `$코덱스플로우`
  - Keep internal package and state dir unchanged for this migration: `codex_flow`, `.codex-flow`

## Plan Quality Check

- Alternative considered: fully renaming repo/package/state from `codex-flow` to `implementation-commit`.
  - Rejected for this pass because package/state renames touch too much runtime and existing local state.
- Why this plan:
  - It changes the user-facing name where the user actually invokes the skill while keeping runtime compatibility.
- What this plan may still miss:
  - Some historical docs will still mention Codex Flow by design.
  - Codex runtime skill discovery behavior should be checked after adding the new skill directory.
- When to stop and revise:
  - If skill discovery fails to expose `구현커밋`, or old `$코덱스플로우` no longer routes to the same behavior.

## Skill Routing Manifest

| Phase | Required skills | Optional skills | Evidence |
| --- | --- | --- | --- |
| Commit 1: 현재 이름 표면과 alias 위험 고정 | `요청개선`, `plan-first-implementation` | `community-research`, `project-wiki-all-in-one` | Current unit narrows the requested migration into an implementation-ready source plan and records `rg`/`find` evidence for current names, aliases, wrappers, and metadata. |
| Commit 2: canonical 구현커밋 skill 추가와 legacy alias 전환 | `plan-first-implementation` | `skill-creator` | `구현커밋/SKILL.md`, old alias `SKILL.md` wrappers, agents metadata |
| Commit 3: CLI wrapper/symlink alias 추가 | `mission-completion-harness` | `테스트` | `scripts/implementation_commit.py`, `scripts/구현커밋.py`, `--help` smoke tests |
| Commit 4: active docs/tests 이름 정합성 갱신 | `content-sync-auditor` | `review-swarm` | README, repo-local skills, installed skills, tests and active docs |
| Final Gate | `review-all-in-one`, `qa-gate` | `테스트` | `python3 -m pytest`, `rg` checks, script help checks, skill discovery/file checks |

## Implementation Plan

### Commit 1: 현재 이름 표면과 alias 위험 고정

- 대상 파일:
  - `docs/plans/2026-06-03-implementation-commit-name-migration-plan.md`
- 변경:
  - 현재 구조에서 `codex-flow`, `코덱스플로우`, `Codex Flow`가 걸려 있는 표면을 기록한다.
  - skill symlink 위험과 runtime rename 위험을 계획에 명시한다.
- 검증:
  - `rg -n "codex-flow|코덱스플로우|Codex Flow|구현커밋" README.md codex_flow scripts skills tests docs`
  - `rg -n "codex-flow|코덱스플로우|Codex Flow|구현커밋" codex-flow 코덱스플로우 scripts README.md` in `/Users/moonsoo/projects/codex-skills-user`
- 성공 기준:
  - 마이그레이션 대상과 의도적 보존 대상이 분리된다.
- 중단 조건:
  - user-facing trigger가 어디서 정의되는지 불명확하면 구현하지 않는다.

### Commit 2: canonical `구현커밋` skill 추가와 legacy alias 전환

- 대상 파일:
  - `/Users/moonsoo/projects/codex-skills-user/구현커밋/SKILL.md`
  - `/Users/moonsoo/projects/codex-skills-user/구현커밋/agents/openai.yaml`
  - `/Users/moonsoo/projects/codex-skills-user/codex-flow/SKILL.md`
  - `/Users/moonsoo/projects/codex-skills-user/코덱스플로우/SKILL.md`
  - `/Users/moonsoo/projects/codex-flow/skills/구현커밋/SKILL.md`
  - `/Users/moonsoo/projects/codex-flow/skills/codex-flow/SKILL.md`
  - `/Users/moonsoo/projects/codex-flow/skills/코덱스플로우/SKILL.md`
- 변경:
  - `구현커밋/SKILL.md`를 canonical body로 만든다.
  - 설명은 "plan-first 문서를 기준으로 구현, 검토, repair, commit을 수행하는 실행 스킬"로 바꾼다.
  - `codex-flow`와 `코덱스플로우`는 canonical `구현커밋`을 읽고 따르는 alias wrapper로 축소한다.
  - 기존 `$코덱스플로우` 호출, `$codex-flow` 호출은 계속 작동한다고 명시한다.
- 검증:
  - `find /Users/moonsoo/projects/codex-skills-user -maxdepth 2 -name SKILL.md | rg "구현커밋|codex-flow|코덱스플로우"`
  - `rg -n "name: 구현커밋|구현커밋|코덱스플로우.*alias|codex-flow.*alias" /Users/moonsoo/projects/codex-skills-user/구현커밋 /Users/moonsoo/projects/codex-skills-user/codex-flow /Users/moonsoo/projects/codex-skills-user/코덱스플로우`
- 성공 기준:
  - `구현커밋` is discoverable as the canonical skill.
  - old skills clearly point to the canonical one.
- 중단 조건:
  - symlink or wrapper causes skill metadata to resolve to the wrong `name:`.

### Commit 3: CLI wrapper/symlink alias 추가

- 대상 파일:
  - `/Users/moonsoo/projects/codex-flow/scripts/implementation_commit.py`
  - `/Users/moonsoo/projects/codex-flow/scripts/구현커밋.py`
  - `/Users/moonsoo/projects/codex-skills-user/scripts/implementation_commit.py`
  - `/Users/moonsoo/projects/codex-skills-user/scripts/구현커밋.py`
  - `codex_flow/cli.py`
- 변경:
  - Runtime은 계속 `/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py`를 호출한다.
  - 새 wrappers or symlinks는 canonical runtime으로 넘긴다.
  - `argparse` `prog`/description은 호출 파일 이름을 반영하거나 최소한 `구현커밋 / implementation-commit` alias를 help에 표시한다.
  - 기존 `scripts/codex_flow.py`는 compatibility wrapper로 유지한다.
- 검증:
  - `python3 /Users/moonsoo/projects/codex-flow/scripts/implementation_commit.py --help`
  - `python3 /Users/moonsoo/projects/codex-flow/scripts/구현커밋.py --help`
  - `python3 /Users/moonsoo/projects/codex-skills-user/scripts/implementation_commit.py --help`
  - `python3 /Users/moonsoo/projects/codex-skills-user/scripts/구현커밋.py --help`
- 성공 기준:
  - New and old wrapper paths all show the same command surface.
  - No wrapper requires package install.
- 중단 조건:
  - Korean filename execution breaks on this environment. If so, keep Korean skill alias and use English script wrapper only.

### Commit 4: active docs/tests 이름 정합성 갱신

- 대상 파일:
  - `README.md`
  - `codex_flow/state.py`
  - `codex_flow/cli.py`
  - `codex_flow/planner_agent.py`
  - `codex_flow/implementer_agent.py`
  - `codex_flow/runner.py`
  - `codex_flow/tickets.py`
  - `codex_flow/merge.py`
  - `codex_flow/pr.py`
  - `codex_flow/__init__.py`
  - active tests that assert visible text
- 변경:
  - User-facing strings prefer `구현커밋`.
  - Internal package names, `.codex-flow`, and old commit prefixes can remain when changing them would break state compatibility.
  - README explains: "구현커밋 was formerly Codex Flow; Codex Flow remains a compatibility alias."
  - Historical docs under old dated plans are left unchanged unless they are active setup instructions.
- 검증:
  - `rg -n "Codex Flow|코덱스플로우|codex-flow" README.md codex_flow scripts skills tests docs/public-release-plan.md docs/*.md`
  - Every remaining hit is classified as either compatibility alias, internal state/package, or historical doc.
- 성공 기준:
  - New users see `구현커밋` first.
  - Old users can still use `$코덱스플로우`.
- 중단 조건:
  - Docs imply old route behavior or old natural-language planning behavior.

### Commit 5: 최종 검증과 테스트 패키징

- 대상 파일:
  - tests touched by Commit 2-4
  - skill docs touched by Commit 2-4
- 변경:
  - No new feature behavior beyond naming/alias migration.
- 검증:
  - `python3 -m pytest`
  - `git diff --check`
  - `git -C /Users/moonsoo/projects/codex-skills-user diff --check -- 구현커밋 codex-flow 코덱스플로우 scripts`
  - Wrapper `--help` checks from Commit 3
  - `rg` remaining-name audit from Commit 4
- 성공 기준:
  - Full tests pass.
  - New name and old aliases both remain documented and callable.
  - Remaining old names are intentional and documented.
- 중단 조건:
  - Old `$코덱스플로우` compatibility breaks.
  - New `구현커밋` skill is not discoverable.
