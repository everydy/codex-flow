---
name: codex-flow
description: "Use when the user wants a Crack-CLI-style automatic development factory or types $코덱스플로우. Includes 라우트 for inbox/existing-plan/new-plan routing, 다음실행 for run-next, 모두실행 for run-all, plus 상태, 브리핑, 리뷰, PR초안, PR생성, 병합, dashboard, PR lock, pr-check, and drain."
metadata:
  short-description: "Crack-CLI형 자동 개발 공장"
---

# Codex Flow

## Purpose

Codex Flow는 큰 작업을 `ticket -> plan/branch -> run-next -> Codex exec -> review decision -> commit -> run-all -> AI preflight repair -> PR/merge -> morning brief`로 나누는 자동 개발 공장 스킬이다.

Crack-CLI를 그대로 복사하지 않고, 사용자의 기존 스킬셋에 맞게 도입한다. 즉 `plan-first-implementation`으로 계획을 잠그고, `mission-completion-harness`의 hard stop gate를 지키면서, Codex App 안에서 내가 CLI를 대신 호출하는 방식으로 운용한다.

현재 구조의 핵심은 `Skill Routing Manifest`다. Codex Flow가 스킬을 다시 구현하지 않고, `plan.md` 안에 commit unit별 필수/선택 스킬을 명시한 뒤 `run-next`, `run-all`, `review`, `PR초안`이 그 manifest를 읽어 실행 프롬프트와 검토 산출물에 반영한다.

중요한 기본값: hard stop은 사용자에게 되묻는 장치가 아니라 AI preflight가 먼저 해결해야 하는 작업 신호다. Codex Flow를 쓸 때는 가능한 경우 `--auto-resolve` 경로로 dirty state, unfinished unit, merge readiness를 먼저 정리하고 계속 진행한다. 단 PR lock은 review gate라서 새 작업을 inbox로 보낸다.

## When To Use

- 사용자가 `$codex-flow`, `Codex Flow`, `run-next`, `run-all`, `morning brief`를 언급한다.
- 사용자가 `$코덱스플로우`, `$코덱스플로우 라우트`, `$코덱스플로우 다음실행`, `$코덱스플로우 모두실행`을 언급한다.
- 사용자가 밤에 작업을 쌓아 두고 낮에 이력을 검토하고 싶어 한다.
- 여러 작업을 티켓처럼 쌓고, Codex가 작은 실행 단위를 실제로 구현/커밋하길 원한다.
- Crack-CLI처럼 밤에 작업을 자동으로 돌리고 낮에 PR/브리프/로그를 검토하고 싶다.
- 실제 원격 PR 생성 전 dry-run 본문이나 리뷰 체크리스트가 필요하다.

## Core Flow

1. 저장소 루트에서 `python3 scripts/codex_flow.py init`으로 `.codex-flow/`를 만든다.
2. `python3 scripts/codex_flow.py route "<요청>"`으로 Codex Router/Planner agent가 티켓과 plan/branch queue를 만든다. 오프라인 smoke test나 deterministic fallback이 필요할 때만 `--router heuristic --planner template`을 붙인다.
3. `python3 scripts/codex_flow.py run-next --plan <plan.md>`로 commit unit 하나를 구현하고 같은 Codex session review 후 자동 커밋한다. 프롬프트만 만들 때는 `--preview`, 큐 변경도 없이 볼 때는 `--dry-run`을 붙인다.
4. `python3 scripts/codex_flow.py run-all --plan <plan.md>`로 plan이 complete 또는 needs_work가 될 때까지 반복 처리하며 각 성공 unit을 자동 커밋한다. preview prompt만 만들 때는 `--preview`를 붙이고, 기본 4개까지만 만든다.
5. 아침에는 `morning-brief`와 `review`로 검토 자료를 만든다.
6. PR 초안은 `open-pr --auto-resolve --dry-run`으로 남은 unit을 먼저 끝내고 자동 커밋한 뒤 만든다. 실제 원격 PR은 `create-pr --auto-resolve`로 분리해 실행한다.
7. merge는 `merge --auto-resolve`로 미완료 unit을 끝내고 자동 커밋한 뒤 readiness를 확인하고 진행한다. 현재 작업 흐름상 원격 통합이 자연스러운 완료 조건이면 `--remote`까지 사용할 수 있다.

## Skill Routing Manifest

`plan.md`에는 아래 형식의 표가 있어야 한다.

```md
## Skill Routing Manifest

| Phase | Required skills | Optional skills | Evidence |
| --- | --- | --- | --- |
| Commit 1: 근거 수집과 범위 잠금 | `요청개선` | `community-research` | 요청 범위와 근거를 잠근다. |
| Commit 2: 좁은 구현 패치 | `mission-completion-harness` | `디자인올인원` | 선택된 구현 단위를 끝까지 완수한다. |
| Final Gate | `review-all-in-one`, `qa-gate` | `checkpoint` | PR/merge 전 검토와 검증을 끝낸다. |
```

실행 규칙:

- Planner agent는 commit unit을 만들 때 이 manifest도 함께 유지한다.
- Implementer prompt는 현재 commit unit의 manifest entry를 포함한다.
- 실행 agent는 필수 스킬을 실제로 읽고 적용한다. 사용할 수 없는 스킬은 가까운 fallback을 쓰고 review summary에 이유를 남긴다.
- `review`와 `PR초안`은 manifest를 다시 보여줘서 낮에 검토할 때 “어떤 스킬이 적용됐는지”를 확인할 수 있게 한다.
- 레인러너는 Build 작업을 Codex Flow에 넘기고, 세부 스킬 라우팅은 이 manifest가 담당한다.

## Korean Shortcut Commands

| 호출 | 역할 | 실행 의미 |
| --- | --- | --- |
| `$코덱스플로우`, `$코덱스플로우 상태` | 상태 확인 | 현재 repo의 Codex Flow를 초기화하고 `status`, `pr-check`를 확인한다. |
| `$코덱스플로우 라우트 <요청>` | 라우트 | 요청을 ticket으로 저장하고 plan queue를 만든다. |
| `$코덱스플로우 다음실행` | 다음실행 | 현재 plan의 다음 incomplete commit unit 하나를 `run-next --auto-resolve`로 실행하고 자동 커밋한다. |
| `$코덱스플로우 모두실행` | 모두실행 | 현재 plan의 incomplete commit unit들을 `run-all --auto-resolve`로 끝까지 실행하고 unit별 자동 커밋한다. |
| `$코덱스플로우 브리핑` | 브리핑 | `morning-brief`로 작업 재개 브리프를 만든다. |
| `$코덱스플로우 리뷰` | 리뷰 | 현재 plan의 review checklist를 만든다. |
| `$코덱스플로우 PR초안` | PR 초안 | 미완료 unit을 자동 실행한 뒤 PR dry-run 산출물을 만든다. |
| `$코덱스플로우 PR생성` | PR 생성 | 미완료 unit을 자동 실행한 뒤 active PR lock이 없을 때 원격 draft PR을 만든다. |
| `$코덱스플로우 병합` | 병합 | 미완료 unit을 자동 실행한 뒤 merge를 진행한다. finalize 흐름이면 remote merge까지 이어갈 수 있다. |

## Crack-CLI Parity Features

- `route`는 PR lock이 있으면 inbox로 보내고, `--plan`이 있으면 기존 plan request queue에 붙이며, active plan이 하나면 그 plan에 자동으로 붙인다.
- 기본 `route`는 Codex Router/Planner agent를 사용한다. `--router heuristic --planner template`은 오프라인 smoke test나 deterministic fallback용이다.
- `run-next`는 구현 후 같은 session review decision을 거친 뒤 기본값으로 commit한다. 커밋을 막아야 하는 특수 상황에서만 `--no-commit`을 쓴다.
- 완료 판정은 `queue.json` 단독이 아니라 `plan.md`의 commit unit과 `log.md`의 `Completed commit unit N` 기록을 기준으로 한다.
- `dashboard`는 PR lock, inbox 수, dirty file 수, active plan 진행률, 최근 log, suggested command를 보여준다.
- `set-pr-lock`, `clear-pr-lock`, `pr-check`, `drain`으로 PR lock lifecycle을 관리한다.
- `run-all --open-pr`는 완료 후 PR dry-run을 만들고, `create-pr`는 active PR lock이 없을 때 원격 draft PR을 만든다.
- `run-all --merge`는 완료 후 local merge를 시도한다.
- successful remote merge는 matching PR lock을 자동으로 clear한다.
- remote PR/remote merge는 현재 작업의 GitHub finalize 흐름에 포함될 수 있으며, CLI에서는 `--remote` 모드로 실행한다.

## Guardrails

- GitHub PR 생성과 remote merge는 Codex Flow의 finalize 단계로 취급한다. 자동 개발 공장, PR, merge, finalize 요청이 현재 작업에 포함되어 있으면 dry-run에 멈추지 않고 진행할 수 있다.
- active PR lock이 있으면 remote PR 생성은 중단하고, 새 작업은 inbox로 보낸다.
- production deploy, 결제, 외부 게시, 계정 작업은 hard stop이다.
- `run-next`와 `run-all`은 기본값으로 Codex CLI를 실제 실행하고 성공한 unit 변경을 자동 커밋한다. 프롬프트만 만들려면 `--preview` 또는 `--dry-run`을 명시한다.
- `--execute`, `--execute-units`, `--commit`은 호환용 명시 플래그다. 커밋을 의도적으로 막을 때만 `--no-commit`을 쓴다.
- 실행 전 worktree가 dirty이면 `--auto-resolve`로 non-`.codex-flow/` 변경을 로컬 stash에 보존하고 계속한다. revert/reset으로 사용자 변경을 삭제하지 않는다.
- PR lock이 있으면 `route --auto-resolve`라도 새 작업을 inbox에 보낸다. PR lock은 review gate라서 임의로 stacked plan을 만들지 않는다.
- PR 생성 전 unit이 미완료이면 `open-pr/create-pr --auto-resolve`로 남은 unit을 실행하고 자동 커밋한 뒤, 끝까지 `done`이 된 경우에만 PR artifact 또는 remote PR을 만든다.
- merge 전 unit이 미완료이면 `merge --auto-resolve`로 남은 unit을 실행하고 자동 커밋한 뒤, 끝까지 `done`이 된 경우에만 merge한다.
- `.codex-flow/`에는 로컬 작업 맥락이 들어갈 수 있으므로 공개 저장소에 올리기 전에 내용을 점검한다.

## Auto-Resolve Policy

| 기존 hard stop | Codex Flow 자동 해결 |
| --- | --- |
| 작업 폴더가 더러우면 실행 안 함 | `run-next/run-all --auto-resolve`가 dirty path를 git stash로 보존한 뒤 계속한다. |
| PR lock이 있으면 새 작업은 inbox | Codex Flow도 inbox에 보낸다. lock 해제 후 `pr-check`/`drain`이 다시 라우팅한다. |
| 모든 unit이 `done` 아니면 PR 생성 안 함 | `open-pr`/`create-pr --auto-resolve`가 남은 unit을 실행, 자동 커밋하고 PR 산출물 또는 원격 PR 생성을 재시도한다. |
| merge가 중간에서 멈춤 | Codex Flow finalize 단계에서는 `merge --auto-resolve`를 사용한다. 이 경로는 미완료 unit을 먼저 해결하고 local merge를 실행하며, 필요하면 `--remote`로 원격 통합까지 진행한다. |

자동 해결 후에도 `needs_work`가 남으면 그것은 사용자 검토 요청이 아니라 새 repair unit의 입력이다. 이때는 실패 이유를 queue/log에 남기고, 다음 `run-next --auto-resolve` 또는 더 좁은 repair plan으로 이어간다.

## Commands

```bash
python3 scripts/codex_flow.py init
python3 scripts/codex_flow.py route "작업 요청"
python3 scripts/codex_flow.py route "작업 요청" --router heuristic --planner template
python3 scripts/codex_flow.py plan --ticket .codex-flow/tickets/<ticket>.md
python3 scripts/codex_flow.py run-next --plan .codex-flow/plans/<slug>/plan.md
python3 scripts/codex_flow.py run-next --plan .codex-flow/plans/<slug>/plan.md --auto-resolve
python3 scripts/codex_flow.py run-next --plan .codex-flow/plans/<slug>/plan.md --preview
python3 scripts/codex_flow.py run-all --plan .codex-flow/plans/<slug>/plan.md --auto-resolve
python3 scripts/codex_flow.py run-all --plan .codex-flow/plans/<slug>/plan.md --preview
python3 scripts/codex_flow.py dashboard
python3 scripts/codex_flow.py dashboard --watch
python3 scripts/codex_flow.py morning-brief
python3 scripts/codex_flow.py review --plan .codex-flow/plans/<slug>/plan.md
python3 scripts/codex_flow.py open-pr --plan .codex-flow/plans/<slug>/plan.md --auto-resolve --dry-run
python3 scripts/codex_flow.py create-pr --plan .codex-flow/plans/<slug>/plan.md --auto-resolve
python3 scripts/codex_flow.py pr-check
python3 scripts/codex_flow.py drain
python3 scripts/codex_flow.py set-pr-lock --branch codex/<slug> --pr-url https://github.com/example/repo/pull/1
python3 scripts/codex_flow.py clear-pr-lock
python3 scripts/codex_flow.py merge --plan .codex-flow/plans/<slug>/plan.md --auto-resolve
```

## Reference

- CLI entrypoint: `scripts/codex_flow.py`
- Package: `codex_flow/`
- Tests: `tests/`
