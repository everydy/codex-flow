# Commit 4A Reviewer Contract Separation Plan

## Goal

Codex Flow의 writable implementation과 post-unit review를 별도 interface와 별도 child session으로 분리한다. reviewer는 fresh read-only process로만 실행하고 파일 수정, implementation session resume, review 중 repair를 금지한다. reviewer 전후 HEAD와 전체 tracked/untracked bytes가 같을 때만 review result를 소비한다.

## Source Of Truth

- [Refined request](../../request-refiner-artifacts/2026-07-20-212409-commit-4a-refined-request.md)
- [Research](research.md#commit-4a-implementation-research-revalidation--2026-07-20)
- [Parent adaptive execution plan](plan.md#commit-4a-reviewer-contract-separation)

이 문서는 상위 계획의 Commit 4A만 실행 가능한 숫자형 unit으로 좁힌 subplan이다. 상위 계획의 4B~4D와 Commit 5 이후를 실행하지 않는다.

## Current Behavior

```text
CodexImplementerAgent.implement
  -> fresh workspace-write implementation
  -> same-session resumed workspace-write review
  -> combined ImplementerAgentResult
```

현재 reviewer는 독립 실행 주체가 아니며 review prompt가 focused fix를 허용한다. `run_codex_exec` resume argv는 새 sandbox argument를 구성하지 않으므로 `sandbox="read-only"` 값을 넘겨도 resume 경로의 권한 분리를 증명할 수 없다.

## Scope

### In scope

- implementer result에서 review payload 제거
- `ReviewerAgent` protocol과 `CodexReadOnlyReviewer` 구현
- runner가 implementer와 reviewer를 순차 호출하고 failure phase를 분리
- internal review machine line을 `INTERNAL_REVIEW_GATE`로 분리
- generic runtime의 automatic `review-all-in-one` skill injection 제거
- reviewer 전후 HEAD·full diff digest invariant
- fresh-session/read-only/no-repair/malformed-gate regression tests
- 기존 runner repair, commit title/summary, review artifact payload 회귀 테스트 보정

### Out of scope

- Commit 4B profile strategy dispatcher, docs lease, verification registry
- Commit 4C final-gate producer와 exact evidence binding
- Commit 4D finalize guard, activation state, canary
- state root 이동, PR/merge 동작 변경, dependency 변경

## Related Files

- `codex_flow/implementer_agent.py`: writable implementation만 실행하도록 축소
- `codex_flow/reviewer_agent.py`: review types, prompt, parser, read-only runtime 계약 소유
- `codex_flow/runner.py`: implementer→reviewer sequencing, failure phase, repair loop 소유
- `tests/test_agent_roles.py`: direct agent contract와 argv/sandbox/session 검증
- `tests/test_runner_brief.py`: runner integration, malformed gate, repair regression
- `tests/test_child_attestation.py`: 같은 prepared child home을 쓰되 다른 fresh session을 쓰는 계약
- `tests/test_execution_worktree.py`, `tests/test_runner_repair_edges.py`: fake Codex fresh-review protocol migration과 회귀

## Preserved Contracts

- implementation은 계속 `sandbox="workspace-write"`다.
- reviewer finding 수정은 reviewer가 아니라 다음 bounded implementer attempt가 수행한다.
- attempt ledger, queue status, repair fingerprint, commit title/summary, review JSON payload ownership은 runner에 남는다.
- child runtime attestation과 prepared child home은 유지한다.
- reviewer 실패나 mutation은 fail-closed `needs_work`이며 candidate diff를 삭제하거나 rollback하지 않는다.

## Proposed API Shape

Illustrative only; exact naming may follow local style.

```python
@dataclass(frozen=True)
class ImplementerAgentResult:
    session_id: str
    implementation_message: str

class ReviewerAgent(Protocol):
    def review(self, input_data: ReviewerAgentInput, ...) -> ReviewerAgentResult: ...

review_result = reviewer.review(
    ReviewerAgentInput(
        implementation_session_id=implementation_result.session_id,
        expected_head=head_summary(repo) or "",
        expected_diff_digest=scoped_diff_digest(repo, []),
        ...,
    )
)
```

```python
review = run_codex_exec(
    build_read_only_review_prompt(input_data),
    repo=input_data.repo,
    sandbox="read-only",
    # resume_session_id is deliberately omitted
    phase="review",
    ...,
)
```

Reviewer prompt output contract:

```text
INTERNAL_REVIEW_GATE status="pass|needs_work" blockers=0 important=0 minor=0 reason="..."
COMMIT_UNIT_READY title="..." summary="..."
```

or one `COMMIT_UNIT_NEEDS_WORK reason="..."` terminal. Reviewer prompt explicitly prohibits edits, repair, commit, PR, merge, and deploy.

## Skill Routing Manifest

| Phase | Required skills | Optional skills | Evidence |
| --- | --- | --- | --- |
| Commit 1: Separate writable implementer and read-only reviewer | `구현커밋` | `superpowers:test-driven-development` | approved 4A contract, runner/agent tests, no profile/finalize expansion |
| Final Gate | `review-all-in-one`, `테스트` | `qa-gate` | actual diff, focused/full related tests, independent read-only review, commit/push packaging |

## Implementation Plan

### Commit 1: Separate writable implementer and read-only reviewer

- target files:
  - `codex_flow/implementer_agent.py`
  - `codex_flow/reviewer_agent.py`
  - `codex_flow/runner.py`
  - `tests/test_agent_roles.py`
  - directly affected fake/integration tests only
- changes:
  1. move review-only result/parsing/prompt ownership to `reviewer_agent.py` while avoiding circular imports.
  2. make `CodexImplementerAgent.implement()` perform only the writable implementation call.
  3. add `CodexReadOnlyReviewer.review()` using a fresh non-resumed `read-only` child call.
  4. reject missing/reused review session identity, malformed `INTERNAL_REVIEW_GATE`, HEAD drift, and full tracked/untracked byte drift as non-retryable protocol failures.
     - `reused` means the fresh reviewer session id equals the current attempt's implementation session id. Historical reviewer session ids from earlier attempts are recorded but are not a uniqueness oracle.
     - run the post-review HEAD/full-digest oracle in a `finally`-equivalent path after success, timeout, nonzero exit, or parse failure. Mutation/protocol evidence takes precedence over the child process error while retaining the underlying error in diagnostics.
  5. call implementer and reviewer sequentially from runner; label process failures with the actual `implementation|review` phase and never commit a review-failure attempt.
  6. keep review findings retryable only through the existing next implementer attempt; reviewer never repairs.
  7. migrate test fakes from `"resume" in args` to review-prompt detection so fresh review calls cannot accidentally execute implementation behavior.
  8. preserve only plan-declared required skills in child runtime attestation. Remove the generic runtime's automatic `review-all-in-one` skill injection and replace the old `review_skill` artifact marker with explicit internal-review evidence.
  9. write the review attempt artifact with at least `review_mode="fresh_read_only"`, implementation/reviewer session ids, `head_unchanged`, `full_diff_digest_unchanged`, the parsed `INTERNAL_REVIEW_GATE`, and the existing child-attestation/ledger bindings. Do not store raw prompts, diffs, tokens, or other secret-bearing payloads.
- verification:
  - `python3 -m pytest tests/test_agent_roles.py tests/test_final_gate.py -q`
    - implementation uses workspace-write once.
    - review uses read-only once, omits resume id, and has a distinct session.
    - review prompt forbids edit/repair and requires internal gate.
    - mutation, HEAD/digest drift, malformed gate, missing/reused session fail closed.
    - reviewer timeout/nonzero still performs the post-review HEAD/full-digest oracle.
    - reviewer timeout/nonzero is reported as `phase=review`, remains non-retryable, and produces zero commits.
  - `python3 -m pytest tests/test_runner_brief.py tests/test_child_attestation.py tests/test_execution_worktree.py tests/test_runner_repair_edges.py -q`
    - repair loop still returns findings to a new implementer attempt.
    - review artifact, title/summary, commit, execution worktree, attestation behavior remain compatible.
    - `required_skills` equals the plan-declared skill set; the generic runtime does not inject `review-all-in-one`.
    - review JSON records the fresh reviewer session and invariant evidence under `INTERNAL_REVIEW_GATE` semantics, without the legacy `review_skill="review-all-in-one"` marker.
  - failure matrix assertions:
    - implementation child timeout/nonzero -> `phase=implementation`, no reviewer call, no commit.
    - reviewer child timeout/nonzero with unchanged bytes -> `phase=review`, held diagnostics, no retry and no commit.
    - reviewer child timeout/nonzero with HEAD or full-digest drift -> non-retryable mutation/protocol failure, underlying child error retained in diagnostics, no commit.
    - reviewer success with malformed gate, reused session, HEAD drift, or byte drift -> fail closed, no commit.
  - `rg -n "review-all-in-one|REVIEW_GATE|resume_session_id|sandbox=\"workspace-write\"" codex_flow/implementer_agent.py codex_flow/reviewer_agent.py codex_flow/runner.py`
    - no automatic `review-all-in-one` reference remains in these runtime paths.
    - no production reviewer call resumes a session or uses workspace-write.
    - legacy `REVIEW_GATE` is not accepted as the new internal review gate.
  - `git diff --check`
- success criteria:
  - no production review call passes `resume_session_id` or `sandbox="workspace-write"`.
  - reviewer cannot change HEAD or tracked/untracked bytes and still return ready.
  - reviewer prompt contains no fix/repair authority and generic runtime no longer requires `review-all-in-one` as its internal engine.
  - blocker/important findings from final independent review are zero.
- stop conditions:
  - any review path still resumes implementation session.
  - read-only mutation is not detectable, repair/commit contract regresses, or fake migration requires weakening assertions.
  - implementation expands into 4B+.
- tradeoff:
  - adopted: runner-owned separate reviewer interface.
  - rejected smaller alternative: fresh reviewer call hidden inside implementer; it preserves lifecycle coupling and weakens the 4B seam.
  - rejected larger alternative: external review command; it splits the current attempt transaction and overlaps 4C.
  - cost: runner plumbing and several fake agents change together.
  - revisit: only if separate-session overhead is empirically dominant after 4A passes.

## Pre-Implementation Review Findings

### Independent `review-all-in-one` / `review-swarm`

- verdict before repair: `보완 후 진행`
- Important 1: reviewer timeout/nonzero paths did not have an executable oracle proving phase attribution, unconditional mutation detection, non-retryability, and zero commits.
- Important 2: automatic `review-all-in-one` removal lacked assertions for exact plan-declared skills and the internal review artifact transition.
- Minor: `reused session` was ambiguous across attempts.
- resolution: the implementation and verification sections now include the full exception/mutation matrix, exact skill-closure assertion, internal review artifact schema, and current-attempt session definition.

### `해결전략검토`

- strongest objection: a reviewer that mutates files and then exits nonzero could bypass an invariant check performed only on the success path, leaving unsafe bytes while the runner reports only a process error.
- verdict: `조건부 적절` before plan repair; `적절` after requiring a finally-equivalent invariant oracle on every reviewer exit path.
- root-cause fit: a fresh read-only reviewer removes the resumed writable lifecycle coupling instead of hiding its symptoms. The full-digest oracle independently detects a sandbox/runtime failure.
- blast radius: limited to implementer/reviewer ownership, runner sequencing/artifacts, and directly affected tests; 4B+ remains excluded.
- rollback/safe-disable: revert the isolated 4A implementation commit while preserving the accepted parent plan and existing candidate diff. No candidate diff is automatically deleted or rolled back on failure.
- kill criteria: any reviewer resume/workspace-write path, any exception path that skips the invariant oracle, weakened assertions, or runtime reliance on generic `review-all-in-one` blocks implementation.
- what would change the verdict: a focused test demonstrating that a failing reviewer can mutate HEAD/bytes without a non-retryable failure would return the plan to `부적절`.

### 선제적 진단 로그 계획

- boundary: runner transitions `implementation_start|implementation_end|review_start|review_end` and reviewer invariant finalization.
- fields: unit/attempt id, phase, sanitized process status, implementation/reviewer session relation (same/different, not raw session contents), expected/observed HEAD, HEAD-unchanged boolean, full-digest-unchanged boolean, ledger revision, terminal gate status.
- abnormal signals: missing/reused reviewer session, review timeout/nonzero, malformed internal gate, HEAD drift, full-digest drift, commit attempt after review failure.
- storage: existing phase diagnostic metadata plus attempt review JSON; do not add a second broad event system in 4A.
- redaction: no prompt, raw diff, token, environment value, or full child output in the structured review artifact.
- test oracle: the failure matrix above asserts phase and invariant fields for success and exception paths. Logging supplements but never replaces the fail-closed assertions.

### Re-review gate

- the two Important findings are resolved in the plan. Implementation may begin only after a quick read-only re-review confirms blocker=0 and important=0.

## Operator 결정 필요 사항

- 상태: 없음
- 기본값: 4A만 구현하고 4B 이후는 각 gate 통과 뒤 별도 진행한다.
- 이유: 현재 codebase와 accepted parent plan에서 범위와 acceptance oracle이 모두 확정돼 있다.

## 검토용 결과물

- primary: this plan Markdown and focused test commands.
- HTML: omitted because this is a Python CLI/runtime contract with no visual UI or browser interaction.
- localhost/deploy: not applicable.

## HTML 생략 보고서

- 판정: 생략 가능
- 사유: reviewer process 권한·session·digest 계약은 코드와 pytest assertion으로 검토하며 HTML이 판단 품질을 높이지 않는다.

## Plan Quality Check

- Alternative considered: implementer-internal fresh review and external two-phase review command.
- Why this plan: runner가 sequencing을 소유하고 agents가 단일 책임을 가지므로 현재 root cause와 4B seam을 함께 만족한다.
- Tradeoff: fake/integration tests 수정량이 늘지만 실제 resumed-review coupling을 제거하는 비용이다.
- What this plan may still miss: real Codex sandbox implementation bug는 unit test만으로 완전히 증명할 수 없다. pre/post digest가 보조 oracle이다.
- When to stop and revise: reviewer process가 read-only인데도 candidate bytes를 바꾸거나, child session identity를 신뢰할 수 없거나, runner repair semantics가 깨질 때.

## 구현 후 검토 리스트

- 회귀 확인: existing implementation, repair, queue, review artifact, commit title/summary, attestation, worktree behavior.
- 검증 확인: focused agent tests, affected runner integration suite, `git diff --check`, no-resume/no-workspace-write residual search.
- 리뷰 관점: privilege separation, mutation oracle, circular dependency, fake-only success, error phase attribution.
- Operator 재확인: 없음. 4A evidence가 통과하면 다음 작업은 4B plan gate다.

## 후행 실행

- `구현커밋`
- this document is the exact source plan for the current request.
