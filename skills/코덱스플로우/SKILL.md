---
name: 코덱스플로우
description: "Use when the user types $코덱스플로우, 상태, 라우트, 다음실행, 모두실행, 브리핑, 리뷰, PR초안, PR생성, 병합, PR체크, or 대시보드. 라우트는 찾을 수 있는 plan-first Markdown 문서를 실행 source로 채택한다. 짧은 자연어 요청으로 새 plan을 만들지 않는다."
metadata:
  short-description: "$코덱스플로우 한국어 호출과 라우트/다음실행/모두실행"
---

# 코덱스플로우

`codex-flow`의 한국어 호출 alias다. 이 스킬이 로드되면 먼저 아래 본체 스킬을 읽고 따른다.

- `/Users/moonsoo/projects/codex-skills-user/codex-flow/SKILL.md`

## Canonical Runtime

- 실제 실행기는 `/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py` 하나다.
- `/Users/moonsoo/projects/codex-skills-user/scripts/codex_flow.py`는 오래된 호출을 살리기 위한 wrapper이며, canonical 실행기로 넘기는 역할만 한다.
- `codex-skills-user/tools/codex_flow` 같은 내장 구현이 다시 보이면 실행하지 말고 stale duplicate로 취급한다.

## 간단 명령

| 호출 | 역할 | 실행 의미 |
| --- | --- | --- |
| `$코덱스플로우 <문서경로>` | 라우트 | plan-first Markdown 문서 경로를 `route <문서경로> --auto-resolve`로 실행한다. |
| `$코덱스플로우 플랜대로 실행` | 라우트 | 현재 요청 또는 직전 대화 맥락에서 단일 plan-first 문서 링크/경로를 찾을 수 있을 때만 그 문서를 route한다. 없거나 여러 개면 plan-first 문서나 Markdown 경로를 다시 달라고 묻고 멈춘다. |
| `$코덱스플로우` | 라우트 대기 | 상태 확인을 실행하지 않는다. 실행할 plan-first 문서나 Markdown 경로가 필요하다고 짧게 말한다. |
| `$코덱스플로우 상태` | 상태 확인 | dashboard 상태와 PR lock 여부를 다시 확인한다. |
| `$코덱스플로우 라우트 <문서경로>` | 라우트 | plan-first Markdown 문서를 source로 채택하고 실행 queue를 만든다. PR lock이 있으면 새 plan을 만들지 않고 보류한다. |
| `$코덱스플로우 다음실행` | 다음실행 | 현재 plan의 다음 incomplete commit unit 하나를 `run-next --auto-resolve`로 실행하고 자동 커밋한다. |
| `$코덱스플로우 모두실행` | 모두실행 | 현재 plan의 incomplete commit unit들을 `run-all --auto-resolve`로 끝까지 실행하고 unit별 자동 커밋한다. |
| `$코덱스플로우 브리핑` | 브리핑 | 오늘 아침/작업 재개용 `morning-brief`를 만든다. |
| `$코덱스플로우 리뷰` | 리뷰 | 현재 plan의 review checklist를 만든다. |
| `$코덱스플로우 PR초안` | PR 초안 | 미완료 unit을 자동 실행한 뒤 `open-pr --dry-run` 산출물을 만든다. |
| `$코덱스플로우 PR생성` | PR 생성 | 미완료 unit을 자동 실행한 뒤 active PR lock이 없을 때 `create-pr`로 원격 draft PR을 만든다. |
| `$코덱스플로우 병합` | 병합 | 미완료 unit을 자동 실행한 뒤 merge를 진행한다. finalize 흐름이면 remote merge까지 이어갈 수 있다. |
| `$코덱스플로우 대시보드` | 대시보드 | active plan, PR lock, inbox, dirty files, suggested command를 보여준다. |
| `$코덱스플로우 PR체크` | PR 체크 | active PR lock의 GitHub 상태를 확인하고 merged면 lock을 풀고 inbox를 drain한다. |

## 실행 규칙

- 사용자가 `$코덱스플로우 상태`라고 말하면 현재 repo에서 `init`, `status`, `pr-check`를 실행한다.
- 사용자가 `$코덱스플로우 <입력>`처럼 호출했고 `<입력>`이 `상태`, `다음실행`, `모두실행`, `브리핑`, `리뷰`, `PR초안`, `PR생성`, `병합`, `대시보드`, `PR체크` 같은 명시 명령이 아니면, 현재 입력이나 직전 대화 맥락에서 plan-first Markdown 문서 링크/경로를 찾아야 한다.
- 현재 입력에 plan-first Markdown 경로/링크가 있으면 `route <문서경로> --auto-resolve`로 실행한다.
- 현재 입력이 `플랜대로 실행`처럼 짧아도, 직전 대화 턴에서 단일 plan-first 문서 링크/경로를 찾을 수 있으면 그 문서로 route한다.
- plan-first 문서를 찾을 수 없거나 후보가 여러 개면 실행하지 않는다. "plan-first 문서를 말해주거나 Markdown 경로를 넘겨 달라"고 묻고 멈춘다.
- 사용자가 `$코덱스플로우`만 말하면 상태 확인을 실행하지 않는다. 실행할 plan-first 문서나 Markdown 경로가 필요하다고 짧게 말하고, 상태 확인은 `$코덱스플로우 상태`를 쓰라고 안내한다.
- `다음실행`과 `모두실행`은 현재 plan이 명확할 때 바로 실행한다.
- `다음실행`과 `모두실행`은 기본값으로 Codex CLI가 실제 구현을 수행하고, 같은 Codex session의 `review-all-in-one` post-unit gate를 통과한 성공 unit만 자동 커밋한다. 프롬프트만 보고 싶을 때만 `--preview` 또는 `--dry-run`을 쓴다.
- 사용자가 `모두실행`을 호출한 뒤에는 후속 안내와 검토 task도 기본적으로 `run-all --auto-resolve` 기준으로 말한다. `run-next`는 사용자가 명시적으로 다음 unit만 원하거나, `run-all`이 특정 failed repair unit을 남겼을 때의 단일-unit repair/manual fallback으로만 제안한다.
- `PR초안`/`PR생성`/`병합`의 `--auto-resolve`는 미완료 unit을 기본값으로 실제 실행하고 자동 커밋한다. 사용자가 명시적으로 커밋 금지를 요청한 경우에만 `--no-commit`을 쓴다.
- `--auto-resolve` 실행 중 unit review가 `needs_work`를 반환하면 같은 unit을 기본 1회 repair context로 재시도한다. 횟수는 `--repair-attempts <n>`으로 조정한다.
- repair budget 후에도 `needs_work`가 남으면 실패 사유와 partial changed paths를 다음 repair 입력으로 유지한다. 다음 `run-next --auto-resolve`는 그 partial changed paths를 stash하지 않는다.
- `브리핑`은 plan이 없어도 실행할 수 있다.
- `리뷰`, `PR초안`, `PR생성`, `병합`은 현재 plan이 명확할 때 실행한다.
- `대시보드`는 `dashboard`를 실행한다.
- `PR체크`는 `pr-check`를 실행한다.
- 현재 plan이 명확하지 않으면 `.codex-flow/plans/*/plan.md` 중 가장 최근 plan을 우선 사용한다.
- route할 plan-first source 문서가 없으면 새 ticket/plan을 만들지 않는다. plan-first 문서나 Markdown 경로를 다시 달라고 말한다.
- `drain`은 유효한 plan-first Markdown 경로만 다시 라우팅한다. 짧은 요청이나 legacy ticket은 새 plan으로 만들지 않는다.
- source plan에서 `### Commit N:` 또는 `### Phase N:` 단위를 찾지 못하면 low-confidence unit을 `human_gate`로 멈춘다. plan-first 문서를 더 작은 단계로 나눈 뒤 다시 route한다.
- `라우트`와 `plan` 결과물에는 `## Skill Routing Manifest`가 있어야 한다. 이 표는 각 commit unit에서 필수로 적용할 스킬과 선택 스킬을 기록한다.
- `다음실행`과 `모두실행`은 현재 unit의 manifest entry를 실행 프롬프트에 포함시키고, 필수 스킬을 적용하거나 fallback 이유를 남기게 한다.
- 모든 실행 commit unit은 커밋 전에 `review-all-in-one` post-unit gate를 통과해야 한다. 이 게이트에서 blocker/important 문제가 나오면 고치거나 `needs_work`로 멈추며 커밋하지 않는다.
- `리뷰`, `PR초안`, `PR생성`, `병합` 전 검토에서는 manifest에 적힌 스킬이 실제로 적용되었는지 확인한다.
- PR 생성과 remote merge는 본체 `codex-flow`의 finalize 흐름을 따른다. deploy, 결제, 외부 게시, 계정 작업은 본체 guardrail을 따른다.

## CLI 매핑

```bash
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> init
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> status
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> pr-check
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> dashboard
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> route docs/plans/example-plan.md --auto-resolve
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> run-next --plan <plan.md> --auto-resolve
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> run-next --plan <plan.md> --preview
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> run-all --plan <plan.md> --auto-resolve
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> run-all --plan <plan.md> --preview
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> run-all --plan <plan.md> --auto-resolve --open-pr
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> run-all --plan <plan.md> --auto-resolve --merge
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> morning-brief
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> review --plan <plan.md>
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> open-pr --plan <plan.md> --auto-resolve --dry-run
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> create-pr --plan <plan.md> --auto-resolve
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py --repo <repo> merge --plan <plan.md> --auto-resolve
```
