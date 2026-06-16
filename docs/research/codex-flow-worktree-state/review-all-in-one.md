# Review All-In-One: Codex Flow Task Worktree State Research

## 짧은 구현 설명

이번 검토 대상은 구현 코드가 아니라 `research.md` 조사 산출물이다.

조사 산출물은 Codex Flow가 현재 `repo/.codex-flow`에 ticket, plan, queue를 만들고, `plan_dir.parents[2]`로 실행 repo를 역산한다는 구조적 문제를 잘 잡았다. 결론도 타당하다. 스킬 문서만 고치는 방식보다, `$구현커밋` route 단계에서 작업별 worktree를 만들고 그 worktree 내부의 `.codex-flow/`를 상태 저장소로 쓰게 하는 런타임 변경이 재발방지에 더 직접적이다.

다만 plan-first로 넘기기 전에 몇 가지 결정을 더 잠가야 한다. 특히 default worktree 위치, 기존 `parents[2]` fallback 전략, PR lock 범위, source drift 경로 보존이 구현 리스크의 핵심이다.

## 상세 검토 결과

### Blocker

발견된 blocker 없음.

research.md는 계획 수립에 필요한 핵심 경계를 충분히 드러낸다. 바로 구현을 시작하기보다는 plan-first 문서에서 결정과 커밋 단위를 잠그면 된다.

### Important

1. PR lock 범위가 아직 계획 결정으로 고정되지 않았다.
   - 근거: `research.md`의 `Open Questions`에 "per task worktree vs repo-global lock/index"가 남아 있다.
   - 위험: task-local `.codex-flow/locks/pr-lock.md`만 두면 여러 worktree에서 동시에 PR 생성이 가능해질 수 있다. 반대로 repo-global lock을 먼저 만들면 이번 변경 범위가 커진다.
   - 판단: 1차 구현은 task-local lock을 유지하고, global index/aggregate lock은 명시적으로 후속 작업으로 미루는 것이 안전하다.

2. source plan path 보존 방식이 plan에 명시되어야 한다.
   - 근거: `source_plan.check_source_drift`가 `directory.parents[2]` 기준으로 상대 경로를 복원한다.
   - 위험: route가 별도 task worktree에서 실행되면 원본 plan-first 문서가 원본 repo에 있을 수 있고, 상대 경로가 task worktree 기준으로 바뀌어 drift check가 틀릴 수 있다.
   - 판단: `source_repo`, `source_plan_path`, `source_plan_sha256`을 metadata에 명시하고, drift check는 이 metadata를 우선해야 한다.

3. `prepare_branch`를 그대로 쓰면 worktree-first 설계를 깨뜨린다.
   - 근거: `git_ops.prepare_branch`는 현재 repo에서 `git switch` 또는 `git switch -c`를 수행한다.
   - 위험: route 중에 원본 worktree branch가 바뀌거나, 기존 dirty root에서 작업 branch가 섞일 수 있다.
   - 판단: `ensure_worktree`를 추가하고, route에서는 branch switching이 아니라 `git worktree add` 또는 기존 worktree 재사용을 먼저 수행해야 한다.

4. `parents[2]` 제거는 한 번에 끝내야 한다.
   - 근거: research.md가 `runner`, `merge`, `pr`, `source_plan`, `plans.mark_unit`의 의존을 확인했다.
   - 위험: 일부만 바꾸면 실행, PR, merge 중 하나가 다른 repo에서 동작한다.
   - 판단: 첫 커밋에서 resolver와 metadata fallback을 추가하고, 이후 커밋에서 모든 caller를 이 resolver로 옮기는 순서가 필요하다.

### Minor

1. dashboard aggregation은 이번 계획의 필수 범위에서 빼도 된다.
   - 근거: per-worktree `.codex-flow`로 가면 dashboard가 task-local이 된다.
   - 판단: 이번 목표는 state 혼합 방지다. 통합 dashboard는 별도 후속 계획으로 남기는 편이 낫다.

2. README와 skill mirror 업데이트는 구현 마지막 커밋에 묶는 것이 좋다.
   - 근거: 런타임 동작이 확정되기 전 문서부터 바꾸면 직전 롤백한 문제를 반복한다.
   - 판단: 테스트가 통과한 뒤 문서와 `skills/구현커밋/SKILL.md`를 갱신한다.

## 다음 task

1. plan-first 문서에서 기본 결정을 잠근다.
   - route가 task worktree를 자동 생성한다.
   - `.codex-flow/`는 task worktree 내부에 둔다.
   - `--repo`는 실행 worktree를 의미한다.
   - default worktree root는 `~/.config/superpowers/worktrees/<repo-name>/<slug>`로 둔다.
   - PR lock은 1차 구현에서 task-local로 유지한다.

2. implementation-ready plan을 만든다.
   - `### Commit N:` 단위로 resolver, worktree creation, runner/finalizer migration, docs/tests를 나눈다.
   - `Skill Routing Manifest`를 포함한다.
   - package-backed test runner는 supply-chain freeze 때문에 실제 실행 전 별도 확인이 필요하다는 점을 검증 계획에 남긴다.

3. 후행 실행은 `$구현커밋`으로 넘긴다.
   - plan-first 문서가 승인되면 이 계획 문서를 source로 route한다.
   - 이번 단계에서는 구현 패치를 시작하지 않는다.
