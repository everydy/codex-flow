# Pre-Implementation Review

## Scope

- source prompt: `docs/request-refiner-artifacts/2026-07-21-implementation-commit-risk-status-followup-refined-request.md`
- research: `research.md`
- reviewed plan: `plan.md`
- code contracts: policy inference, queue/ledger ownership, dashboard rendering, existing tests
- mode: read-only `review-all-in-one` using structure, state/data, regression, test and operator-safety perspectives; no subagent was used because the delegation gate selected single-agent execution.

## Verdict

- score: 88/100 before findings incorporation; 94/100 after plan update
- blockers: 0
- important: 2, both incorporated
- minor: 2, both resolved in plan
- implementation gate: proceed to `해결전략검토`

## Findings

### [Important] Test-scope normalization needs a traversal guard

- evidence: existing documentation normalization uses `lstrip("./")`, which is not suitable as a trust boundary for a new test-root oracle.
- risk: `../tests/...` or an absolute path could be normalized into an apparently safe root, incorrectly lowering text-based risk.
- disposition: plan now requires rejecting absolute paths and any `..` component before accepting only `test/` or `tests/` roots.
- test gap added: traversal, absolute, mixed test/product and high-risk product paths.

### [Important] Dashboard must not print raw failure text

- evidence: `last_needs_work_reason` can contain exception/diagnostic strings; ledger events deliberately allowlist and redact fields.
- risk: raw display can leak environment/path details or create unbounded dashboard output.
- disposition: plan now requires existing diagnostic redaction, one-line normalization and a strict length cap; raw prompt/diff/stdout/stderr remains forbidden.
- test gap added: multiline/oversized/secret-like reason is bounded and the underlying queue/ledger bytes remain unchanged.

### [Minor] Suggested `run-all` command is not a parallelism defect

- evidence: `RunAllRunner` processes sequentially and returns `main_handoff` as soon as a parent-direct unit opens.
- risk: changing command wording would expand scope without solving either accepted defect.
- disposition: explicitly excluded from Commit 2.

### [Minor] Status precision must distinguish evidence from fallback

- evidence: isolated ledgers expose phase/heartbeat elapsed; main-direct legacy units may only expose queue `updated_at`.
- risk: labeling a fallback as exact runtime timing would be misleading.
- disposition: dashboard wording must identify queue-derived elapsed/progress as update-based fallback and use `not recorded` where evidence is absent.

## Code Contract Review

- policy safety floor remains in `classify_execution_policy()` and is not edited.
- automatic scope inference remains in `infer_profile_lower_bound()`; no caller-specific override is added.
- test-root absence oracle returns `CONTRACT`, never `DOCS_ONLY`.
- mixed test/product paths do not qualify for the absence oracle.
- dashboard only reads documented queue/ledger locations and catches missing/corrupt data.
- no queue, ledger, event or runtime adapter schema change is planned.

## Required Test Matrix

- docs exact path + risk example + declared docs-only → docs-only/main/final-only.
- docs exact path + risk example + no declaration → contract/main/final-only.
- tests-root path + risk example → contract/main/final-only.
- mixed tests + product path + risk example → high-risk/isolated/per-unit.
- product or `_HIGH_RISK_PATH` case → high-risk/isolated/per-unit.
- declared/persisted high-risk → non-lowerable.
- traversal/absolute pseudo-test path → not accepted as test scope.
- main active dashboard → owner/status/update elapsed/verification.
- isolated live ledger → owner/phase/heartbeat elapsed/last event.
- missing/corrupt ledger → honest fallback; render succeeds.
- dashboard render → queue and ledger bytes unchanged.

## Remaining Risks

- actual token savings require later real-run observation; this patch proves policy selection, not historical token totals.
- legacy plans without execution metadata can only show fallback state.

## Implementation Readiness

The plan is complete enough for an adversarial strategy gate. No implementation file was changed during this review.
