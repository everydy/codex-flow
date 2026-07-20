# 2026-06-03 | nested run-all observability and routing fix plan

## 목적

`run-all`이 내부 `codex exec` 자식 프로세스에서 출력 없이 멈춘 것처럼 보였던 문제를 재발 방지한다. 이번 계획은 현재 코드베이스를 다시 검토한 뒤 정합성을 맞춘 실행 계획이다.

핵심 목표:

- 자식 Codex 실행이 멈추거나 실패했을 때 증거를 남긴다.
- 실패한 unit은 `needs_work`로 멈추고 다음 unit으로 넘어가지 않는다.
- plan-first 원본 문서의 `Skill Routing Manifest`와 대상 파일 범위가 queue에 보존된다.
- 한글 경로와 repo 밖 절대 경로 때문에 자동 실행 범위가 흔들리지 않는다.

## 코드베이스 흐름 검토

### route 흐름

현재 source plan adoption 흐름은 아래 순서다.

```txt
cli.py route
  -> source_plan.resolve_source_plan()
  -> plans.create_plan_from_source()
    -> plan_first_extract.extract_tickets()
    -> source_plan.snapshot_source_plan()
    -> plan_first_extract.write_ticket_files()
    -> plans.queue_from_source_tickets()
    -> plans.write_source_plan_files()
    -> plans.ensure_plan_skill_routing_manifest()
    -> plan_readiness.sync_queue_cache_from_plan()
```

정합성 포인트:

- `queue_from_source_tickets()`에서 source plan의 실행 단위가 queue unit으로 처음 바뀐다.
- `write_source_plan_files()`에서 generated `plan.md`가 만들어진다.
- `ensure_plan_skill_routing_manifest()`와 `sync_queue_cache_from_plan()`이 이후 queue를 다시 만질 수 있으므로, 이 두 단계가 source manifest와 allowed paths를 덮어쓰지 않아야 한다.

### run-all 실행 흐름

현재 nested run execution 흐름은 아래 순서다.

```txt
cli.py run-all
  -> RunAllRunner.run_all()
    -> runner.run_all()
      -> runner.run_next()
        -> runner.execute_unit()
          -> CodexImplementerAgent.implement()
            -> run_codex_exec()  # implementation
            -> run_codex_exec()  # review resume
          -> parse_commit_unit_review()
          -> plans.save_queue()
```

정합성 포인트:

- timeout과 diagnostic path는 `codex_cli.run_codex_exec()`에서 만들어야 한다.
- unit 상태 전환은 `runner.execute_unit()`에서 해야 한다.
- `run_all()`은 이미 `human_gate`, `needs_work`, `source_drift`에서 멈추므로, timeout도 `needs_work` action으로 반환되면 기존 정지 정책을 재사용할 수 있다.

### 현재 dirty 코드가 이미 해결한 일부 표면

현재 worktree에는 관련 변경이 이미 있다.

- `codex_flow/git_ops.py`
  - `git status --porcelain=v1 -z`를 사용하도록 바뀌었다.
  - `parse_status_z()`가 추가되어 한글/비ASCII 경로 처리를 개선한다.
- `codex_flow/plan_first_extract.py`
  - source plan excerpt의 backtick code span에서 경로 후보를 뽑는 `extract_allowed_paths()`가 추가됐다.
- `codex_flow/plans.py`
  - source route 시 `extract_allowed_paths()` 결과를 unit `allowed_paths`에 반영하기 시작했다.
- `tests/test_agent_git_ops.py`
  - 한글 경로 status/stash 테스트가 추가됐다.
- `tests/test_route_plan_first_source.py`
  - source plan의 대상 파일 code span을 allowed paths로 반영하는 테스트가 추가됐다.

### 남은 불일치

- 현재 신규 테스트는 repo 밖 절대 경로가 `allowed_paths`에 들어가는 동작을 기대한다. 이 계획의 최종 정책은 repo 밖 경로를 자동 수정 대상에서 빼는 것이므로, Commit 1에서 기대값을 바꿔야 한다.
- `Skill Routing Manifest` 보존은 아직 구현되지 않았다.
- `run_codex_exec()`는 아직 timeout, diagnostic artifact, stable stdout/stderr log를 지원하지 않는다.
- `CodexImplementerAgent.implement()`는 implementation과 review를 모두 실행하지만 각 phase의 diagnostic directory를 분리하지 않는다.
- `cli.py`의 `run-next`, `run-all`, `open-pr`, `merge` auto-resolve 경로에는 아직 `--codex-timeout-seconds`가 없다.
- `ImplementerAgentInput.git_status`는 전체 status 문자열이라 unrelated dirty 파일을 prompt에 과하게 노출할 수 있다.

## 데이터 모델 계약

### queue unit

구현 후 queue unit은 아래 필드를 안정적으로 보존할 수 있어야 한다.

```json
{
  "id": "unit-001",
  "number": 1,
  "title": "Source manifest 보존",
  "status": "ready",
  "allowed_paths": ["codex_flow/plans.py", "tests/test_route_plan_first_source.py"],
  "external_allowed_paths": ["/Users/moonsoo/projects/other-repo/docs/plan.md"],
  "required_skills": ["plan-first-implementation", "review-all-in-one"],
  "optional_skills": ["qa-gate"],
  "skill_routing_evidence": "Copied from source plan Skill Routing Manifest.",
  "diagnostic_path": ".codex-flow/plans/example/attempts/unit-001/attempt-0/implementation",
  "last_needs_work_reason": "codex exec timed out after 900s",
  "changed_paths": []
}
```

규칙:

- `allowed_paths`는 현재 repo 안에서 자동 수정 가능한 경로만 담는다.
- `external_allowed_paths`는 repo 밖 경로를 잃지 않기 위한 정보 보존용이다.
- `external_allowed_paths`가 있으면 자동 실행은 `human_gate`로 멈추거나, 최소한 `skill_routing_evidence`와 log에 operator 확인 필요를 남긴다.
- `diagnostic_path`는 실패 때 필수다. 성공 때도 남길 수 있지만, 최소 성공 기준은 실패 진단 가능성이다.

### attempt directory

```txt
.codex-flow/plans/<slug>/attempts/
  unit-001/
    attempt-0/
      implementation/
        prompt.md
        args.json
        metadata.json
        stdout.log
        stderr.log
        last-message.txt
      review/
        prompt.md
        args.json
        metadata.json
        stdout.log
        stderr.log
        last-message.txt
```

`metadata.json` 최소 필드:

```json
{
  "unit_id": "unit-001",
  "attempt": 0,
  "phase": "implementation",
  "started_at": "2026-06-04T12:00:00Z",
  "finished_at": "2026-06-04T12:15:00Z",
  "timeout_seconds": 900,
  "status": "timeout",
  "elapsed_seconds": 900
}
```

## Skill Routing Manifest

| Phase | Required skills | Optional skills | Evidence |
| --- | --- | --- | --- |
| Commit 1: 현재 routing/path 보강 정합성 고정 | `plan-first-implementation`, `review-all-in-one` | `qa-gate` | 현재 dirty 코드가 `git_ops.py`, `plan_first_extract.py`, `plans.py`, route tests를 이미 건드리고 있으므로 정책을 먼저 잠근다. |
| Commit 2: Source manifest 보존 | `plan-first-implementation` | `review-all-in-one` | source plan의 specialist skill routing이 queue/generated plan으로 이어져야 implementer가 올바른 skill을 읽는다. |
| Commit 3: Codex exec diagnostic artifact와 timeout 추가 | `plan-first-implementation`, `mission-completion-harness` | `qa-gate` | `codex_cli.py`와 `git_ops.py`가 무기한 대기 대신 관측 가능한 실패를 만들어야 한다. |
| Commit 4: timeout/failure unit을 needs_work로 고정 | `plan-first-implementation` | `review-all-in-one` | `runner.py`, `run_all.py`, `cli.py`가 실패를 queue/log 상태로 보존해야 run-all이 안전하게 멈춘다. |
| Commit 5: implementer prompt 노이즈 축소 | `plan-first-implementation` | `content-sync-auditor` | `implementer_agent.py`의 git status 블록이 unrelated dirty 파일을 과하게 노출하지 않도록 한다. |
| Commit 6: 문서와 전체 회귀 테스트 정리 | `review-all-in-one`, `qa-gate` | `테스트` | README, skill docs, pytest로 새 실행 계약을 검증한다. |
| Final Gate | `review-all-in-one`, `qa-gate` | `테스트` | 전체 diff, 테스트, 계획 문서 정합성을 다시 확인한다. |

## Implementation Plan

## 코드 스니펫 사용 규칙

아래 코드 스니펫은 구현자가 방향을 빠르게 이해하게 하는 설계 예시다. 그대로 복사해야 하는 최종 API가 아니다. 실제 구현 때는 현재 dataclass, helper, 테스트 fixture에 맞게 조정한다.

### Commit 1: 현재 routing/path 보강 정합성 고정

- 대상 파일:
  - `codex_flow/git_ops.py`
  - `codex_flow/plan_first_extract.py`
  - `codex_flow/plans.py`
  - `tests/test_agent_git_ops.py`
  - `tests/test_route_plan_first_source.py`
- 변경:
  - `parse_status_z()`에 rename/copy case 테스트를 추가한다.
  - repo 내부 절대 경로는 repo-relative로 바꾼다.
  - repo 밖 절대 경로는 `allowed_paths`가 아니라 `external_allowed_paths`에 넣는다.
  - `queue_from_source_tickets()`는 `external_allowed_paths`가 있으면 unit evidence에 operator 확인 필요를 남긴다.
  - 현재 `test_source_route_derives_allowed_paths_from_commit_target_files`의 기대값을 새 정책에 맞게 바꾼다.
- 코드 스니펫:

```python
@dataclass(frozen=True)
class ExtractedPaths:
    allowed_paths: list[str]
    external_allowed_paths: list[str]


def extract_allowed_paths(excerpt: str, repo: str | Path) -> ExtractedPaths:
    repo_path = Path(repo).expanduser().resolve()
    allowed: list[str] = []
    external: list[str] = []
    for raw in extract_code_span_candidates(excerpt):
        candidate = normalize_allowed_path_candidate(raw, repo_path)
        if not candidate:
            continue
        if Path(candidate).is_absolute():
            external.append(candidate)
        else:
            allowed.append(candidate)
    return ExtractedPaths(dedupe(allowed), dedupe(external))
```

- 검증:
  - `python3 -m pytest tests/test_agent_git_ops.py tests/test_route_plan_first_source.py`
- 성공 기준:
  - 한글 경로 dirty file을 stash할 수 있다.
  - repo 내부 target path는 queue `allowed_paths`에 들어간다.
  - repo 밖 절대 경로는 queue `external_allowed_paths`에 들어간다.
- 중단 조건:
  - cross-repo 자동 수정을 실제로 지원해야 한다는 요구가 확인되면 이 commit을 멈추고 별도 operator decision으로 뺀다.

### Commit 2: Source manifest 보존

- 대상 파일:
  - `codex_flow/plans.py`
  - `codex_flow/plan_readiness.py`
  - `tests/test_route_plan_first_source.py`
  - `tests/test_plan_readiness.py`
- 변경:
  - source plan Markdown에서 `## Skill Routing Manifest`를 읽는다.
  - `Commit N:` 또는 `Phase N:` 번호가 queue unit과 일치하면 source manifest 값을 우선 적용한다.
  - source manifest row가 없을 때만 `DEFAULT_UNITS` fallback을 사용한다.
  - `ensure_plan_skill_routing_manifest()`와 `sync_queue_cache_from_plan()`이 source specialist skills를 덮어쓰지 않는지 테스트한다.
- 코드 스니펫:

```python
def source_manifest_by_number(source_content: str) -> dict[int, plan_readiness.SkillRoutingEntry]:
    mapped: dict[int, plan_readiness.SkillRoutingEntry] = {}
    for entry in plan_readiness.parse_skill_routing_manifest(source_content):
        number = parse_commit_or_phase_number(entry.phase)
        if number is not None:
            mapped[number] = entry
    return mapped


manifest_entry = manifest_by_number.get(index)
if manifest_entry:
    unit["required_skills"] = list(manifest_entry.required_skills)
    unit["optional_skills"] = list(manifest_entry.optional_skills)
    unit["skill_routing_evidence"] = manifest_entry.evidence
```

- 검증:
  - `python3 -m pytest tests/test_route_plan_first_source.py tests/test_plan_readiness.py tests/test_plans.py`
- 성공 기준:
  - source plan에 `flow-architecture-map`, `structure-map-html` 같은 specialist skill이 있으면 queue와 generated plan에도 유지된다.
  - manifest 없는 기존 계획은 fallback behavior를 유지한다.
- 중단 조건:
  - 번호 매칭이 실패하거나 ambiguous하면 자동 보존 대신 `human_gate`로 멈추게 한다.

### Commit 3: Codex exec diagnostic artifact와 timeout 추가

- 대상 파일:
  - `codex_flow/git_ops.py`
  - `codex_flow/codex_cli.py`
  - `codex_flow/implementer_agent.py`
  - `tests/test_codex_cli.py`
  - `tests/test_agent_roles.py`
- 변경:
  - `run_process()`에 optional `timeout_seconds`를 추가하되 기본값은 `None`으로 둬 기존 호출부를 깨지 않는다.
  - `run_codex_exec()`에 optional `timeout_seconds`와 `diagnostic_dir`를 추가한다.
  - `run_codex_exec()`는 planner/merge agent도 사용하므로, `diagnostic_dir`가 없으면 기존처럼 임시 stable output을 만들고 동작해야 한다.
  - diagnostic dir가 있으면 `prompt.md`, `args.json`, `stdout.log`, `stderr.log`, `last-message.txt`, `metadata.json`을 남긴다.
  - implementation과 review phase는 같은 attempt 아래 다른 하위 폴더를 쓴다.
- 코드 스니펫:

```python
class CodexExecTimeout(RuntimeError):
    def __init__(self, elapsed_seconds: int, diagnostic_dir: Path) -> None:
        super().__init__(f"codex exec timed out after {elapsed_seconds}s; diagnostics: {diagnostic_dir}")
        self.elapsed_seconds = elapsed_seconds
        self.diagnostic_dir = diagnostic_dir


def run_codex_exec(
    prompt: str,
    repo: str | Path,
    *,
    timeout_seconds: int | None = None,
    diagnostic_dir: Path | None = None,
    phase: str = "codex-exec",
    ...
) -> CodexExecResult:
    paths = prepare_diagnostic_paths(diagnostic_dir, phase)
    paths.prompt.write_text(prompt, encoding="utf-8")
    paths.args.write_text(json.dumps(args, ensure_ascii=False, indent=2), encoding="utf-8")
    result = run_process(args, cwd=repo_path, input_text=prompt, env=env, timeout_seconds=timeout_seconds)
```

- 검증:
  - `python3 -m pytest tests/test_codex_cli.py tests/test_agent_roles.py`
- 성공 기준:
  - 기존 fake codex 정상 종료 테스트가 그대로 통과한다.
  - timeout fake codex는 `CodexExecTimeout`을 발생시키고 diagnostic files를 남긴다.
  - planner/merge agent의 기존 `run_codex_exec()` 호출은 수정 없이 동작하거나 최소 변경만 필요하다.
- 중단 조건:
  - `subprocess.run(timeout=...)`으로 partial stdout/stderr 보존이 부족하면 `subprocess.Popen` streaming 방식으로 전환한다.

### Commit 4: timeout/failure unit을 needs_work로 고정

- 대상 파일:
  - `codex_flow/cli.py`
  - `codex_flow/runner.py`
  - `codex_flow/run_all.py`
  - `codex_flow/implementer_agent.py`
  - `tests/test_runner_brief.py`
  - `tests/test_crack_parity.py`
- 변경:
  - `run-next`, `run-all`, `open-pr --auto-resolve`, `merge --auto-resolve`에 `--codex-timeout-seconds`를 연결한다.
  - `runner.execute_unit()`가 attempt dir를 만들고 `CodexImplementerAgent.implement()`에 전달한다.
  - `CodexExecTimeout`과 nonzero child failure를 잡아 unit을 `needs_work`로 바꾼다.
  - queue에 `last_needs_work_reason`, `diagnostic_path`, `changed_paths`, `updated_at`을 남긴다.
  - CLI 출력에 `diagnostic_path`를 포함한다.
- 코드 스니펫:

```python
try:
    agent_result = agent.implement(
        input_data,
        diagnostic_dir=attempt_dir,
        timeout_seconds=codex_timeout_seconds,
    )
except CodexExecTimeout as exc:
    unit["status"] = "needs_work"
    unit["last_needs_work_reason"] = str(exc)
    unit["diagnostic_path"] = str(exc.diagnostic_dir.relative_to(plan_dir))
    unit["changed_paths"] = repair_changed_paths(preserved_repair_dirty, before, status(repo))
    plans.save_queue(plan_dir, queue_data)
    append_log(plan_dir, f"Timeout in {unit['id']}: {exc}")
    return {
        "unit": unit,
        "action": "needs_work",
        "reason": str(exc),
        "diagnostic_path": exc.diagnostic_dir,
    }
```

- 검증:
  - `python3 -m pytest tests/test_runner_brief.py tests/test_crack_parity.py`
- 성공 기준:
  - fake timeout 시 `run-next`가 `needs_work`를 반환한다.
  - `run-all`은 timeout unit 뒤 다음 unit으로 넘어가지 않는다.
  - CLI 출력에서 diagnostic path를 볼 수 있다.
- 중단 조건:
  - timeout 처리 중 auto-resolve stash 경계가 불명확해지면 status transition을 먼저 재설계한다.

### Commit 5: implementer prompt 노이즈 축소

- 대상 파일:
  - `codex_flow/git_ops.py`
  - `codex_flow/runner.py`
  - `codex_flow/implementer_agent.py`
  - `tests/test_agent_roles.py`
  - `tests/test_runner_brief.py`
- 변경:
  - runner가 unit `allowed_paths`를 기준으로 scoped git status를 만든다.
  - `ImplementerAgentInput.git_status`에 전체 status가 아니라 scoped summary를 전달한다.
  - unrelated dirty file 이름은 prompt에 직접 노출하지 않고 count만 남긴다.
- 코드 스니펫:

```python
def scoped_status_summary(snapshot: GitStatusSnapshot, allowed_paths: list[str]) -> str:
    matching = [entry.raw for entry in snapshot.entries if path_allowed(entry.path, allowed_paths)]
    hidden_count = len(snapshot.entries) - len(matching)
    lines = matching or ["Clean within allowed paths"]
    if hidden_count:
        lines.append(f"... {hidden_count} unrelated dirty path(s) hidden from implementer prompt")
    return "\n".join(lines)
```

- 검증:
  - `python3 -m pytest tests/test_agent_roles.py tests/test_runner_brief.py`
- 성공 기준:
  - allowed path 안의 dirty file은 prompt에 보인다.
  - unrelated dirty file은 prompt에 직접 노출되지 않는다.
- 중단 조건:
  - scoped summary 때문에 안전 경고가 약해지면 file name만 숨기고 dirty count와 hard stop은 유지한다.

### Commit 6: 문서와 전체 회귀 테스트 정리

- 대상 파일:
  - `README.md`
  - `/Users/moonsoo/projects/codex-skills-user/codex-flow/SKILL.md`
  - `/Users/moonsoo/projects/codex-skills-user/코덱스플로우/SKILL.md`
  - `docs/plans/2026-06-03-nested-run-all-observability-and-routing-fix-plan.md`
- 변경:
  - timeout, diagnostic path, source manifest 보존, external path 정책을 문서화한다.
  - nested run이 멈춘 것처럼 보일 때 확인할 파일 순서를 추가한다.
  - 새 dependency를 추가하지 않았다는 점을 명시한다.
- 검증:
  - `python3 -m pytest`
  - `git diff --check`
- 성공 기준:
  - 사용자가 `needs_work`와 `diagnostic_path`만 보고도 다음 조사 지점을 찾을 수 있다.
  - 전체 테스트가 통과한다.
- 중단 조건:
  - 문서의 CLI 옵션명이 실제 구현과 달라지면 문서 커밋을 멈추고 구현명부터 확정한다.

## Operator 결정 필요 사항

상태: 없음.

### 결정 1: 기본 자식 Codex timeout

- 맥락: 너무 짧으면 정상 장시간 구현이 실패하고, 너무 길면 멈춤 감지가 늦다.
- A/B/C:
  - A. 600초
  - B. 900초
  - C. 1800초
- 추천안: B. 900초
- 기본값: B. 900초
- 보류 시 영향: 별도 지시가 없으면 900초로 구현하고 CLI 옵션으로 override한다.

### 결정 2: repo 밖 절대 경로 처리

- 맥락: plan source에는 `/Users/...` 절대 경로가 들어올 수 있다. repo 밖 경로를 자동 allowed path에 넣으면 다른 저장소를 실수로 수정할 수 있다.
- A/B/C:
  - A. `external_allowed_paths`에 보존하고 자동 실행은 `human_gate` 또는 operator 확인 evidence
  - B. repo 밖 경로도 자동 allowed path로 허용
  - C. repo 밖 경로는 버리고 evidence에만 기록
- 추천안: A
- 기본값: A
- 보류 시 영향: cross-repo 자동 수정은 멈추지만, 경로 정보는 보존된다.

## 검토용 결과물

- 계획 문서: `/Users/moonsoo/projects/codex-flow/docs/plans/2026-06-03-nested-run-all-observability-and-routing-fix-plan.md`
- 테스트 링크: 해당 없음. CLI/backend orchestration 작업이므로 localhost URL 대신 pytest 명령으로 검증한다.

### HTML 생략 보고서

- 판정: 생략 가능.
- 이유: 이번 작업은 local web UI나 시각 디자인 변경이 아니라 `codex-flow` CLI의 실행 안정성, 로그 보존, queue routing 정합성 수정이다.
- 대체 검토물: 이 계획 MD, pytest 결과, `.codex-flow/plans/<slug>/attempts/...` 진단 파일 구조.

## Plan Quality Check

- Alternative considered: `run-all`을 쓰지 않고 수동 실행만 권장하는 접근. 재발 방지와 사용자 피드백 루프가 개선되지 않으므로 채택하지 않는다.
- Alternative considered: timeout만 추가하는 접근. source manifest 손실, allowed path drift, diagnostic file 부재는 그대로 남으므로 충분하지 않다.
- Why this plan: 실제 문제는 “멈춤 자체”와 “멈춤을 해석할 증거 부족”이 함께 있었다. timeout, artifact, status transition, manifest preservation, path scoping을 한 흐름으로 묶어야 다음 실패가 디버깅 가능한 실패가 된다.
- What this plan may still miss: 자식 Codex가 stdout은 계속 쓰지만 의미 있는 진행이 없는 경우는 timeout만으로는 stall을 정밀 판별하지 못할 수 있다.
- When to stop and revise: fake timeout 테스트에서 partial stdout/stderr가 보존되지 않거나, source manifest 매칭이 ambiguous한 계획에서 잘못된 skill을 자동 배정하면 구현을 멈추고 매칭 정책을 다시 설계한다.

## 코드베이스 정합성 체크리스트

- [ ] `run_codex_exec()` 변경이 `planner_agent.py`, `merge_agent.py`, `implementer_agent.py` 호출부를 깨지 않는다.
- [ ] `run_process()` signature 변경이 테스트 monkeypatch와 기존 호출부를 깨지 않는다.
- [ ] `sync_queue_cache_from_plan()`이 `allowed_paths`, `external_allowed_paths`, `diagnostic_path`, `changed_paths`를 보존한다.
- [ ] `Skill Routing Manifest` row와 `### Commit N:` heading 번호가 일치한다.
- [ ] `run_all()`은 `needs_work` action에서 이미 멈추므로 timeout은 같은 action으로 연결한다.
- [ ] repo 밖 절대 경로 정책이 테스트 기대값과 문서에서 같은 의미로 쓰인다.
- [ ] 전체 구현은 package install 없이 표준 라이브러리와 기존 pytest만 사용한다.
