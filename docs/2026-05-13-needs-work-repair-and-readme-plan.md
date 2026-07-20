# 2026-05-13 needs_work Repair And README Cleanup Plan

## 목적

Codex Flow의 현재 문서와 실행 동작 사이에 남은 두 가지 불일치를 좁힌다.

- `README.md`의 stale 문장을 현재 실행 기본값에 맞춘다.
- `needs_work`를 단순 중단 상태로만 남기지 않고, `--auto-resolve` 실행에서는 제한된 repair attempt로 이어지게 한다.

## 검토용 결과물

- 상태: verified
- 실제 동작:
  - CLI와 테스트로 검증하는 백엔드 동작 변경
  - `README.md` 문장 정합성 변경
- Mock:
  - 없음

## HTML 생략 보고서

- 판정: 생략 가능
- 생략 사유:
  - 이번 작업은 CLI/backend orchestration 로직과 README 문장 수정이다.
  - 화면, 레이아웃, 인터랙션 검토가 없으므로 HTML artifact보다 테스트 로그가 적합하다.
- 대체 검토물:
  - 계획 MD: `docs/2026-05-13-needs-work-repair-and-readme-plan.md`
  - 검증 명령: `python3 -m compileall codex_flow`, `python3 -m pytest`
- 사용자가 바로 열어볼 링크:
  - 이 계획 파일과 테스트 결과 요약

## 현재 근거

- `codex_flow/runner.py`는 implementer review가 `needs_work`이면 unit을 `needs_work`로 표시하고 바로 반환한다.
- `codex_flow/run_all.py`는 steps 중 `needs_work`가 있으면 전체 run을 `needs_work`로 종료한다.
- `README.md`는 아직 `explicit execution flags`를 Codex Flow의 차이점으로 설명한다. 현재 CLI는 `run-next`와 `run-all`이 기본 실행이고, `--preview`/`--dry-run`이 prompt-only escape hatch다.

## 범위

### 포함

- `README.md`
- `codex_flow/cli.py`
- `codex_flow/runner.py`
- `codex_flow/run_all.py`
- `codex_flow/implementer_agent.py`
- `tests/test_runner_brief.py`

### 제외

- `--branch-mode local|remote` 도입
- remote PR/merge UX 재설계
- Codex Flow 공개 릴리스 문구 전체 재작성
- `.codex-flow/` state format의 대규모 마이그레이션

## 구현 계획

### 1. README stale 문장 수정

- `explicit execution flags` 표현을 제거한다.
- 새 의미는 다음으로 고정한다.
  - 실행 기본값은 `run-next`/`run-all`의 actual execution이다.
  - prompt-only는 `--preview` 또는 `--dry-run` escape hatch로 남는다.
  - dirty worktree 보존은 `--auto-resolve`의 `git stash` 정책으로 설명한다.

### 2. Repair attempt 입력 모델 추가

- `ImplementerAgentInput`에 아래 필드를 추가한다.
  - `repair_attempt: int = 0`
  - `repair_reason: str = ""`
- `build_implementation_prompt()`가 `repair_attempt > 0`일 때 repair context를 포함한다.
- repair prompt는 기존 partial changes를 보존하고, failure reason만 좁게 해결하라고 지시한다.

### 3. Runner repair loop 추가

- `runner.run_next()`와 `runner.run_all()`에 `repair_attempts` 인자를 추가한다.
- `execute_unit()`은 첫 attempt가 `needs_work`이면, 남은 budget이 있을 때 같은 unit을 다시 실행한다.
- 중간 실패는 `Commit unit N needs_work` 로그로 쓰지 않는다. 최종 실패일 때만 기존 needs_work 로그 패턴을 남긴다.
- 성공 시 하나의 run-next 결과로 반환한다.
  - `action`: `committed` 또는 `done` 또는 `skipped`
  - `repair_attempts`: 실제 repair attempt 수
  - `repair_reason`: 최초/마지막 repair reason

### 4. CLI 기본값 연결

- `--repair-attempts <n>` 옵션을 추가한다.
- 기본값:
  - `--auto-resolve`가 있으면 `1`
  - 없으면 `0`
- `run-next`, `run-all`, `open-pr/create-pr --auto-resolve`, `merge --auto-resolve`에 같은 정책을 적용한다.
- 음수 값은 CLI에서 중단한다.

### 5. 테스트

- 기존 `needs_work` 테스트는 auto-resolve 없이 계속 실패해야 한다.
- 새 테스트:
  - `run-all --auto-resolve`가 첫 review `needs_work` 이후 한 번 repair하고 완료한다.
  - repair budget을 모두 써도 실패하면 `needs_work`로 멈추고 무한 반복하지 않는다.
- 기존 default execution tests가 계속 통과해야 한다.

## 성공 기준

- `README.md`에 더 이상 stale `explicit execution flags` 설명이 없다.
- `run-all --auto-resolve`는 transient `needs_work`를 한 번 repair하고 다음 unit으로 계속 진행한다.
- repair 실패가 반복되면 정해진 budget 이후 `needs_work`로 멈춘다.
- 기존 prompt-only escape hatch는 유지된다.
- 전체 pytest가 통과한다.

## 검증 명령

```bash
python3 -m compileall codex_flow
git diff --check
python3 -m pytest
```

## Skill Activation Audit

- Requested lane: Build
- Selected lane: Build
- Mode: build-autonomous
- Requested skills: `plan-first-implementation`, `레인러너`
- Used skills: `plan-first-implementation`, `레인러너`
- Skipped skills: `디자인올인원`, `qa-gate`
- Skip reasons:
  - `디자인올인원`: UI/UX 변경이 아님
  - `qa-gate`: 배포/릴리스 판단이 아니라 좁은 CLI 회귀 테스트가 충분함
- Expected but omitted: 없음
- Verification:
  - `python3 -m compileall codex_flow`
  - `git diff --check`
  - `python3 -m pytest` -> 54 passed
