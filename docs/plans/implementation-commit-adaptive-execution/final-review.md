# Final cumulative review — Main-first convergence

## Verdict

`PASS` — blocker `0`, important `0`, minor `1`.

Reviewed code HEAD: `4de1f43971396c8ab5642a9c2aca248a1af20660`.

This is the single cumulative review required by the `final_only` default. It covers the retained 4A isolation adapter, Commit 4B main-owned transaction, Commit 4C review policy, Commit 4D final gate, and convergence provenance. It does not re-run `review-all-in-one` for every implementation commit.

## Contract findings

### Main-first execution

- Ordinary docs/contract units serialize `executor_adapter=main` and hand control to `begin-main-unit` / `complete-main-unit` / `hold-main-unit`.
- `run-next --execute` returns a main handoff instead of constructing an implementation child.
- High-risk or explicitly isolated work still selects the 4A isolated-child adapter.

Result: pass.

### Scope, Git and state integrity

- Main transactions bind full HEAD, queue revision, owner, allowed paths and out-of-scope digest.
- Completion rejects stale/replayed ownership and out-of-scope drift, and commits only declared paths.
- A failed candidate is held in place; no reset, automatic rollback or cleanup is introduced.

Result: pass.

### Review and recovery policy

- Ordinary units default to `final_only`; `per_unit` is limited to high-risk or explicit selection.
- `needs_work` cannot automatically repair because retry requires an explicit main recovery-owner approval and bounded repair paths. The runner does not provide those approvals implicitly.
- Evaluator/reviewer mutation protection from 4A remains intact.

Result: pass.

### Finalization gate

- Actual remote PR creation and local/remote merge effects require a fresh gate bound to exact HEAD, normalized plan digest and terminal queue revision.
- Evidence is written by temp-file + `fsync` + atomic replace and corrupt/stale/failed records fail closed.
- Only the evidence digest is persisted; raw token, cookie or credential material is not written to the gate.

Result: pass.

## Regression and test-gap review

- Static compile and conflict/secret-marker scans found no actionable finding.
- Focused main-policy/final-gate/runner tests passed `81/81`.
- Full repository suite passed `242/242` on the reviewed HEAD.
- The prior attempt to name non-existent `tests/test_merge.py` and `tests/test_pr.py` was a command-selection error, not a product failure; the real merge/PR gate coverage lives in `tests/test_final_gate.py` and the full suite.

## Minor finding

`retry_eligible()` retains a dormant `fnmatch` path matcher even though automatic recovery is disabled by default and the current runner never supplies approval or repair paths. This is not a release blocker. If explicit recovery is enabled later, its path semantics should be unified with the main transaction's canonical allowed-path matcher before activation.

## Review gate

`REVIEW_GATE status="pass" blockers=0 important=0 minor=1 reason="Main-first ownership, final-only review, no implicit repair, exact scope/HEAD state, atomic same-HEAD finalization evidence, and retained high-risk isolation are covered by code and passing tests."`
