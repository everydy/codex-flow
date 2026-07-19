---
name: 구현커밋
description: "Use when the user wants the canonical 구현커밋 workflow orchestrator for plan-first implementation, commit-unit execution, review, repair, PR dry-run, PR creation, merge readiness, or morning briefs."
metadata:
  short-description: "구현커밋 canonical plan-first implementation and commit-unit orchestrator"
---

# 구현커밋

## Purpose

`구현커밋` is the canonical user-facing skill for plan-first implementation, review, repair, and commit-unit orchestration.

구현커밋는 큰 작업을 `ticket -> plan/branch -> run-next -> Codex exec -> review decision -> commit -> run-all -> AI preflight repair -> local merge -> branch close -> morning brief`로 나누는 자동 개발 공장 스킬이다.

Crack-CLI를 그대로 복사하지 않고, 사용자의 기존 스킬셋에 맞게 도입한다. 즉 `plan-first-implementation`으로 계획을 잠그고, `mission-completion-harness`의 hard stop gate를 지키면서, Codex App 안에서 내가 CLI를 대신 호출하는 방식으로 운용한다.

현재 구조의 핵심은 `Skill Routing Manifest`다. 구현커밋는 스킬을 다시 구현하지 않고, `plan.md` 안에 commit unit별 필수/선택 스킬을 명시한 뒤 `run-next`, `run-all`, `review`, `PR초안`이 그 manifest를 읽어 실행 프롬프트와 검토 산출물에 반영한다.

중요한 기본값: hard stop은 사용자에게 되묻는 장치가 아니라 AI preflight가 먼저 해결해야 하는 작업 신호다. 구현커밋을 쓸 때는 가능한 경우 `--auto-resolve` 경로로 dirty state, unfinished unit, merge readiness를 먼저 정리하고 계속 진행한다. 플랜의 모든 unit이 완료되면 기본값은 작업 브랜치를 target branch, 기본 `main`, 에 local merge한 뒤 `git branch -d`로 닫는 것이다. 단 PR lock은 review gate라서 새 작업을 inbox로 보낸다.

## Canonical Naming

- Canonical user-facing skill name: `구현커밋`
- English alias: `implementation-commit`
- Internal package, state directory, and runtime script remain unchanged for compatibility: `codex_flow`, `.codex-flow`, `/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py`
- New docs and human-facing explanations should lead with `구현커밋`. Old skill aliases are removed and should not be routed as skill calls.

## Canonical Runtime

- 실행 backend는 `/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py` 하나다.
- `codex-skills-user/scripts/implementation_commit.py`와 `codex-skills-user/scripts/구현커밋.py`는 위 canonical entrypoint로 넘긴다.
- `codex-skills-user/tools/codex_flow` 내장 구현은 제거된 stale copy다. 다시 생기면 현재 runtime으로 쓰지 말고 중복 구현으로 취급한다.
- 이름 마이그레이션 중에도 `codex_flow`, `.codex-flow`, `scripts/codex_flow.py`는 바꾸지 않는다. 이 이름들은 runtime compatibility surface다.

## Child Codex Runtime Selection

- 구현커밋 child process는 별도 모델을 지정하지 않으면 parent config에서 안전하게 허용된 model/reasoning/tier 설정과 target repo의 trusted-project 설정을 따른다. 명시 profile과 custom provider는 managed copy 대상이 아니다.
- 기본 경로에서는 호출자가 `CODEX_FLOW_CHILD_HOME`과 `CODEX_FLOW_CHILD_MANIFEST`를 모두 unset 상태로 둔다. runner가 shared pre-edit boundary에서 exact required skill closure를 준비하고, `${CODEX_CHILD_RUNTIME_ROOT:-$CODEX_HOME/child-runtimes}/codex-flow/<repo-hash>/<closure-hash>`의 content-addressed child `CODEX_HOME`을 attestation, implementation, resumed review에 동일하게 전달한다. `<repo-hash>` config-only 디렉터리 자체는 prepared closure가 아니다.
- managed child는 안전한 user model/reasoning/tier 설정, target repo trust, parent `auth.json` symlink만 이어받는다. required standalone skill은 parent registry에서 private staging으로 복사하고, fresh app-server inventory로 standalone/plugin/external ownership을 exact manifest에 고정한다. parent 전역 hooks, unrelated personal skills, MCP 설정을 임의로 재주입하지 않는다. trusted-project config와 `AGENTS.md`는 작업 repo에서 계속 읽는다.
- cache key는 repo identity, normalized required ids, copied source tree hashes, plugin payload hashes와 ids, external exact ids, sanitized config digest, Codex CLI version, command/args, manifest schema가 바뀌면 달라진다. 같은 key는 검증된 published closure를 reuse하며, staging과 cache 디렉터리는 `0700`, manifest/config/lock은 `0600`이다.
- 명시 profile 또는 custom provider가 필요한 경우 repo 밖에 미리 준비한 `CODEX_FLOW_CHILD_HOME`과 `CODEX_FLOW_CHILD_MANIFEST`를 **둘 다** 지정한다. 이 explicit pair는 validation-only override이며 수정하지 않는다. 하나만 지정한 half pair, parent home 재사용, repo-local home, required closure mismatch는 implementer launch나 edit 전에 `needs_work`로 끝난다.
- canonical `run-next`/`run-all` managed preparation은 child isolation을 필수로 하므로 `CODEX_FLOW_CHILD_ISOLATION=0`은 우회 수단이 아니다. profile/custom-provider 오류에서 isolation을 끄거나 다른 모델로 조용히 fallback하지 않는다.
- plan `log.md`에는 credential/config payload 없이 `child_runtime event=prepare|reuse source=<managed|explicit> cache_key=<digest>`를 남긴다. 준비 또는 attestation 거부는 `child_runtime event=deny reason=<exception-class>`와 `changed_paths=[]`를 남기며, 성공 attestation JSON은 plan의 `attestations/<unit>/<nonce>.json`에 저장한다.
- 작업별 모델이 필요하면 `CODEX_FLOW_MODEL` 또는 `--codex-arg=--model`/`-m`으로 명시한다. 명시 인자는 환경변수보다 우선하며 값은 치환하지 않는다.
- 구현커밋은 reasoning effort, service tier, fast mode를 기본으로 강제하지 않는다. Codex 설정 또는 명시적인 `--codex-arg`가 선택한다.
- 모델/config/auth/CLI 호환 오류는 다른 모델로 조용히 fallback하지 않고 해당 unit을 `needs_work`로 멈춘다. review repair budget도 소비하지 않는다.
- attempt의 `args.json`과 `metadata.json`에는 child 인자와 선택 출처(`explicit`, `environment`, `inherited`)를 남긴다. 상속된 실제 모델이 child output으로 확인되지 않았다면 effective model이라고 추측해 기록하지 않는다.

## When To Use

- 사용자가 `$구현커밋`, `구현커밋`, `implementation-commit`, `implementation_commit`를 언급한다.
- 사용자가 `run-next`, `run-all`, `morning brief`를 언급하며 구현커밋 실행 흐름을 원한다.
- 사용자가 밤에 작업을 쌓아 두고 낮에 이력을 검토하고 싶어 한다.
- 여러 작업을 티켓처럼 쌓고, Codex가 작은 실행 단위를 실제로 구현/커밋하길 원한다.
- Crack-CLI처럼 밤에 작업을 자동으로 돌리고 낮에 PR/브리프/로그를 검토하고 싶다.
- 실제 원격 PR 생성 전 dry-run 본문이나 리뷰 체크리스트가 필요하다.

## Core Flow

1. 저장소 루트에서 `/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py init`으로 `.codex-flow/`를 만든다.
2. `/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py route <plan-first.md>`로 승인된 plan-first Markdown 문서를 실행 source로 채택하고 plan/branch queue를 만든다. 짧은 자연어 요청으로 새 plan을 만들지 않는다.
3. `/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py run-next --plan <plan.md>`로 commit unit 하나를 구현하고 같은 Codex session에서 `review-all-in-one` post-unit gate를 통과한 뒤 자동 커밋한다. 프롬프트만 만들 때는 `--preview`, 큐 변경도 없이 볼 때는 `--dry-run`을 붙인다.
4. `/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py run-all --plan <plan.md>`로 plan이 complete 또는 needs_work가 될 때까지 반복 처리하며 각 성공 unit을 `review-all-in-one` post-unit gate 후 자동 커밋한다. 모든 unit이 완료되면 별도 `--merge` 없이 target branch, 기본 `main`, 로 local merge하고 성공한 작업 브랜치를 `git branch -d`로 닫는다. `--auto-resolve`에서는 transient needs_work를 기본 1회 repair하고, preview prompt만 만들 때는 `--preview`, 완료 후 브랜치를 일부러 남길 때만 `--no-merge`를 붙인다.
5. 긴 Codex child process는 `--codex-timeout-seconds`로 제한한다. 기본값은 900초다. timeout 또는 nonzero child failure가 나면 해당 unit은 `needs_work`가 되고, `diagnostic_path`에 prompt/args/stdout/stderr/last-message/metadata 파일 위치가 남는다.
6. 아침에는 `morning-brief`와 `review`로 검토 자료를 만든다.
7. PR 초안은 `open-pr --auto-resolve --dry-run`으로 남은 unit을 먼저 끝내고 자동 커밋한 뒤 만든다. 실제 원격 PR은 `create-pr --auto-resolve`로 분리해 실행한다.
8. 별도 merge 명령은 중단된 finalize를 재개하거나 remote 통합이 필요한 경우에만 쓴다. 평상시 `$구현커밋 모두실행`과 `run-all --auto-resolve`는 완료 후 local merge와 branch close까지 진행한다. 현재 작업 흐름상 원격 통합이 자연스러운 완료 조건이면 `--remote`까지 사용할 수 있다.

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
- 모든 실행 commit unit은 커밋 전에 `review-all-in-one` post-unit gate를 통과해야 한다. 이 게이트에서 blocker/important 문제가 나오면 고치거나 `needs_work`로 멈추며 커밋하지 않는다.
- review agent는 가능하면 `REVIEW_GATE status="pass|needs_work" blockers=0 important=0 minor=0 reason="..."` 줄을 남긴다. 구현커밋은 이 줄을 읽어 `100 - blockers*50 - important*20 - minor*5` 점수를 기록하고, `status="pass"`, `blockers=0`, `important=0`일 때만 커밋한다.
- `review`와 `PR초안`은 manifest를 다시 보여줘서 낮에 검토할 때 “어떤 스킬이 적용됐는지”를 확인할 수 있게 한다.
- 구현커밋은 Build 작업의 실행 source를 맡고, 세부 스킬 라우팅은 이 manifest가 담당한다.

## Shortcut Commands

| 호출 | 역할 | 실행 의미 |
| --- | --- | --- |
| `$구현커밋 <문서경로>` | 라우트 | plan-first Markdown 문서 경로를 `route <문서경로> --auto-resolve`로 실행한다. |
| `$구현커밋 플랜대로 실행` | 라우트 | 현재 요청 또는 직전 대화 맥락에서 단일 plan-first 문서 링크/경로를 찾을 수 있을 때만 그 문서를 route한다. 없거나 여러 개면 plan-first 문서나 Markdown 경로를 다시 달라고 묻고 멈춘다. |
| `$구현커밋` | 라우트 대기 | 상태 확인을 실행하지 않는다. 실행할 plan-first 문서나 Markdown 경로가 필요하다고 짧게 말한다. |
| `$구현커밋 상태` | 상태 확인 | 현재 repo의 구현커밋 상태를 초기화하고 `status`, `pr-check`를 확인한다. |
| `$구현커밋 라우트 <문서경로>` | 라우트 | plan-first Markdown 문서를 source로 채택하고 실행 queue를 만든다. |
| `$구현커밋 다음실행` | 다음실행 | 현재 plan의 다음 incomplete commit unit 하나를 `run-next --auto-resolve`로 실행하고 자동 커밋한다. |
| `$구현커밋 모두실행` | 모두실행 | 현재 plan의 incomplete commit unit들을 `run-all --auto-resolve`로 끝까지 실행하고 unit별 자동 커밋한 뒤, 완료되면 target branch로 local merge하고 작업 브랜치를 닫는다. |
| `$구현커밋 브리핑` | 브리핑 | `morning-brief`로 작업 재개 브리프를 만든다. |
| `$구현커밋 리뷰` | 리뷰 | 현재 plan의 review checklist를 만든다. |
| `$구현커밋 PR초안` | PR 초안 | 미완료 unit을 자동 실행한 뒤 PR dry-run 산출물을 만든다. |
| `$구현커밋 PR생성` | PR 생성 | 미완료 unit을 자동 실행한 뒤 active PR lock이 없을 때 원격 draft PR을 만든다. |
| `$구현커밋 병합` | 병합 | 미완료 unit을 자동 실행한 뒤 merge를 진행한다. finalize 흐름이면 remote merge까지 이어갈 수 있다. |

## Guardrails

- `$구현커밋 <입력>`은 plan-first source route 요청이다. `<입력>`이 `상태`, `다음실행`, `모두실행`, `브리핑`, `리뷰`, `PR초안`, `PR생성`, `병합`, `대시보드`, `PR체크` 같은 명시 명령이 아니면, 현재 입력이나 직전 대화 맥락에서 plan-first Markdown 문서 링크/경로를 찾아야 한다.
- 현재 입력에 plan-first Markdown 경로/링크가 있으면 `route <문서경로> --auto-resolve`로 실행한다.
- 현재 입력이 `플랜대로 실행`처럼 짧아도, 직전 대화 턴에서 단일 plan-first 문서 링크/경로를 찾을 수 있으면 그 문서로 route한다.
- plan-first 문서를 찾을 수 없거나 후보가 여러 개면 실행하지 않는다. "plan-first 문서를 말해주거나 Markdown 경로를 넘겨 달라"고 묻고 멈춘다.
- `$구현커밋`만 있고 요청문이 없으면 상태 확인을 실행하지 않는다. 실행할 plan-first 문서나 Markdown 경로가 필요하다고 짧게 안내하고, 상태 확인은 `$구현커밋 상태`로만 실행한다.
- GitHub PR 생성과 remote merge는 구현커밋의 finalize 단계로 취급한다. 자동 개발 공장, PR, merge, finalize 요청이 현재 작업에 포함되어 있으면 dry-run에 멈추지 않고 진행할 수 있다.
- active PR lock이 있으면 remote PR 생성은 중단한다. 새 source plan route는 lock 해제 전까지 보류한다.
- production deploy, 결제, 외부 게시, 계정 작업은 hard stop이다.
- `run-next`와 `run-all`은 기본값으로 Codex CLI를 실제 실행하고 성공한 unit 변경을 자동 커밋한다. 프롬프트만 만들려면 `--preview` 또는 `--dry-run`을 명시한다.
- 완료된 `run-all`은 기본값으로 local merge와 branch close까지 수행한다. 브랜치를 남겨야 할 때만 `--no-merge`를 명시한다.
- 사용자가 `run-all` 또는 `$구현커밋 모두실행`을 호출한 뒤에는 후속 안내와 검토 task도 기본적으로 `run-all --auto-resolve` 기준으로 말한다. `run-next`는 명시적인 single-unit execution 또는 `run-all`이 남긴 failed repair unit을 처리하는 manual fallback으로만 제안한다.
- `--execute`, `--execute-units`, `--commit`은 호환용 명시 플래그다. 커밋을 의도적으로 막을 때만 `--no-commit`을 쓴다.
- 실행 전 worktree가 dirty이면 `--auto-resolve`로 non-`.codex-flow/` 변경을 로컬 stash에 보존하고 계속한다. revert/reset으로 사용자 변경을 삭제하지 않는다.
- `--auto-resolve` 실행 중 unit review가 `needs_work`를 반환하면 같은 unit을 기본 1회 repair context로 재시도한다. 횟수는 `--repair-attempts <n>`으로 조정한다.
- PR lock이 있으면 `route --auto-resolve`라도 새 plan을 만들지 않는다. PR lock은 review gate라서 임의로 stacked plan을 만들지 않는다.
- PR 생성 전 unit이 미완료이면 `open-pr/create-pr --auto-resolve`로 남은 unit을 실행하고 자동 커밋한 뒤, 끝까지 `done`이 된 경우에만 PR artifact 또는 remote PR을 만든다.
- merge 전 unit이 미완료이면 `merge --auto-resolve`로 남은 unit을 실행하고 자동 커밋한 뒤, 끝까지 `done`이 된 경우에만 merge한다.
- `.codex-flow/`에는 로컬 작업 맥락이 들어갈 수 있으므로 공개 저장소에 올리기 전에 내용을 점검한다.

## Auto-Resolve Policy

| 기존 hard stop | 구현커밋 자동 해결 |
| --- | --- |
| 작업 폴더가 더러우면 실행 안 함 | `run-next/run-all --auto-resolve`가 dirty path를 git stash로 보존한 뒤 계속한다. |
| PR lock이 있으면 새 source plan route는 보류 | 구현커밋은 source Markdown 경로만 inbox에 남긴다. lock 해제 후 `pr-check`/`drain`은 유효한 source plan만 라우팅하고, 짧은 요청은 그대로 남긴다. |
| 모든 unit이 `done` 아니면 PR 생성 안 함 | `open-pr`/`create-pr --auto-resolve`가 남은 unit을 실행, 자동 커밋하고 PR 산출물 또는 원격 PR 생성을 재시도한다. |
| 완료된 브랜치가 남음 | 기본 `run-all --auto-resolve`가 local merge와 `git branch -d` branch close를 수행한다. 이미 완료된 plan이면 `merge --auto-resolve`로 같은 close path를 재시도한다. |
| merge가 중간에서 멈춤 | 구현커밋 finalize 단계에서는 `merge --auto-resolve`를 사용한다. 이 경로는 미완료 unit을 먼저 해결하고 local merge를 실행하며, 필요하면 `--remote`로 원격 통합까지 진행한다. |
| review가 transient `needs_work`를 반환 | `--auto-resolve`가 같은 unit을 기본 1회 repair context로 재실행한다. |

기본 repair attempt 후에도 `needs_work`가 남으면 그것은 사용자 검토 요청이 아니라 더 좁은 repair unit의 입력이다. 이때는 실패 이유와 partial changed paths를 queue/log에 남기고, 다음 `run-next --auto-resolve --repair-attempts <n>` 또는 더 좁은 repair plan으로 이어간다. 다음 repair 실행에서는 그 partial changed paths를 stash하지 않고 repair 입력으로 유지한다.

source plan에서 `### Commit N:` 또는 `### Phase N:` 단위를 찾지 못하면 구현커밋은 low-confidence 단일 unit을 만들되 `human_gate`로 멈춘다. 이 상태에서는 자동 실행하지 말고 plan-first 문서를 더 작은 단계로 나눈 뒤 다시 route한다.

## Commands

```bash
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py init
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py route docs/plans/example-plan.md
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py plan --ticket .codex-flow/tickets/<ticket>.md
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py run-next --plan .codex-flow/plans/<slug>/plan.md
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py run-next --plan .codex-flow/plans/<slug>/plan.md --auto-resolve
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py run-next --plan .codex-flow/plans/<slug>/plan.md --auto-resolve --codex-timeout-seconds 900
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py run-next --plan .codex-flow/plans/<slug>/plan.md --preview
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py run-all --plan .codex-flow/plans/<slug>/plan.md --auto-resolve
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py run-all --plan .codex-flow/plans/<slug>/plan.md --auto-resolve --no-merge
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py run-all --plan .codex-flow/plans/<slug>/plan.md --auto-resolve --codex-timeout-seconds 900
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py run-all --plan .codex-flow/plans/<slug>/plan.md --preview
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py dashboard
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py dashboard --watch
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py morning-brief
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py review --plan .codex-flow/plans/<slug>/plan.md
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py open-pr --plan .codex-flow/plans/<slug>/plan.md --auto-resolve --dry-run
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py create-pr --plan .codex-flow/plans/<slug>/plan.md --auto-resolve
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py pr-check
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py drain
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py set-pr-lock --branch codex/<slug> --pr-url https://github.com/example/repo/pull/1
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py clear-pr-lock
/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py merge --plan .codex-flow/plans/<slug>/plan.md --auto-resolve
```

## Reference

- CLI entrypoint: `/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py`
- Package: `/Users/moonsoo/projects/codex-flow/codex_flow/`
- Tests: `/Users/moonsoo/projects/codex-flow/tests/`
- Managed child operational contract: `docs/plans/codex-flow-managed-child-runtime/operational-contract.md`
- Raw no-env CLI proof: `docs/plans/codex-flow-managed-child-runtime/raw-cli-proof.md`
- Canonical installed skill path: `/Users/moonsoo/projects/codex-skills-user/구현커밋/SKILL.md`
- Repo mirror skill path: `/Users/moonsoo/projects/codex-flow/skills/구현커밋/SKILL.md`
