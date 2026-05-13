---
name: 코덱스플로우
description: "Use when the user types $코덱스플로우, 상태, 라우트, 다음실행, 모두실행, 브리핑, 리뷰, PR초안, PR생성, 병합, PR체크, or 대시보드. 라우트는 inbox/existing-plan/new-plan으로 요청을 보내고, 다음실행은 run-next, 모두실행은 run-all을 실행한다."
metadata:
  short-description: "$코덱스플로우 한국어 호출과 라우트/다음실행/모두실행"
---

# 코덱스플로우

`codex-flow`의 한국어 호출 alias다. 이 스킬이 로드되면 먼저 아래 본체 스킬을 읽고 따른다.

- `../codex-flow/SKILL.md`

## 간단 명령

| 호출 | 역할 | 실행 의미 |
| --- | --- | --- |
| `$코덱스플로우` | 상태 확인 | 현재 repo의 Codex Flow를 `init`하고 `status`, `pr-check`를 확인한다. |
| `$코덱스플로우 상태` | 상태 확인 | dashboard 상태와 PR lock 여부를 다시 확인한다. |
| `$코덱스플로우 라우트 <요청>` | 라우트 | 요청을 ticket으로 저장하고 plan queue를 만든다. PR lock이 있으면 inbox에 보낸다. |
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

- 사용자가 `$코덱스플로우`만 말하거나 `$코덱스플로우 상태`라고 말하면 현재 repo에서 `init`, `status`, `pr-check`를 실행한다.
- `라우트` 뒤에 요청문이 있으면 그 요청문을 그대로 `route "<요청>" --auto-resolve`에 넣는다. 기본값은 Codex Router/Planner agent다.
- 오프라인 smoke test나 deterministic fallback이 필요할 때만 `route "<요청>" --router heuristic --planner template`을 쓴다.
- `다음실행`과 `모두실행`은 현재 plan이 명확할 때 바로 실행한다.
- `다음실행`/`모두실행`/`PR초안`/`PR생성`/`병합`은 기본값으로 Codex CLI를 실제 실행하고 성공한 unit을 자동 커밋한다. 사용자가 명시적으로 커밋 금지를 요청한 경우에만 `--no-commit`을 쓴다.
- 프롬프트만 보고 싶으면 `run-next/run-all --preview` 또는 `--dry-run`을 명시한다.
- `--auto-resolve` 실행 중 unit review가 `needs_work`를 반환하면 같은 unit을 기본 1회 repair context로 재시도한다. 횟수는 `--repair-attempts <n>`으로 조정한다.
- repair budget 후에도 `needs_work`가 남으면 실패 사유와 partial changed paths를 다음 repair 입력으로 유지한다. 다음 `run-next --auto-resolve`는 그 partial changed paths를 stash하지 않는다.
- `브리핑`은 plan이 없어도 실행할 수 있다.
- `리뷰`, `PR초안`, `PR생성`, `병합`은 현재 plan이 명확할 때 실행한다.
- `대시보드`는 `dashboard`를 실행한다.
- `PR체크`는 `pr-check`를 실행한다.
- 현재 plan이 명확하지 않으면 `.codex-flow/plans/*/plan.md` 중 가장 최근 plan을 우선 사용한다.
- plan이 하나도 없으면 먼저 사용자의 요청을 ticket/plan으로 만들기 위해 `$코덱스플로우 라우트 <요청>` 형식이 필요하다고 짧게 말한다.
- `라우트`와 `plan` 결과물에는 `## Skill Routing Manifest`가 있어야 한다. 이 표는 각 commit unit에서 필수로 적용할 스킬과 선택 스킬을 기록한다.
- `다음실행`과 `모두실행`은 현재 unit의 manifest entry를 실행 프롬프트에 포함시키고, 필수 스킬을 적용하거나 fallback 이유를 남기게 한다.
- `리뷰`, `PR초안`, `PR생성`, `병합` 전 검토에서는 manifest에 적힌 스킬이 실제로 적용되었는지 확인한다.
- PR 생성과 remote merge는 본체 `codex-flow`의 finalize 흐름을 따른다. deploy, 결제, 외부 게시, 계정 작업은 본체 guardrail을 따른다.

## CLI 매핑

```bash
python3 scripts/codex_flow.py --repo <repo> init
python3 scripts/codex_flow.py --repo <repo> status
python3 scripts/codex_flow.py --repo <repo> pr-check
python3 scripts/codex_flow.py --repo <repo> dashboard
python3 scripts/codex_flow.py --repo <repo> route "<요청>" --auto-resolve
python3 scripts/codex_flow.py --repo <repo> route "<요청>" --router heuristic --planner template
python3 scripts/codex_flow.py --repo <repo> run-next --plan <plan.md> --auto-resolve
python3 scripts/codex_flow.py --repo <repo> run-next --plan <plan.md> --preview
python3 scripts/codex_flow.py --repo <repo> run-all --plan <plan.md> --auto-resolve
python3 scripts/codex_flow.py --repo <repo> run-all --plan <plan.md> --preview
python3 scripts/codex_flow.py --repo <repo> run-all --plan <plan.md> --auto-resolve --open-pr
python3 scripts/codex_flow.py --repo <repo> run-all --plan <plan.md> --auto-resolve --merge
python3 scripts/codex_flow.py --repo <repo> morning-brief
python3 scripts/codex_flow.py --repo <repo> review --plan <plan.md>
python3 scripts/codex_flow.py --repo <repo> open-pr --plan <plan.md> --auto-resolve --dry-run
python3 scripts/codex_flow.py --repo <repo> create-pr --plan <plan.md> --auto-resolve
python3 scripts/codex_flow.py --repo <repo> merge --plan <plan.md> --auto-resolve
```
