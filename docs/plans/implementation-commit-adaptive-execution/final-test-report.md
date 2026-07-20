# Final test report — Main-first convergence

## Reviewed candidate

- Branch: `codex/implementation-commit-main-first-convergence`
- Pre-evidence HEAD: `4de1f43971396c8ab5642a9c2aca248a1af20660`
- Python: `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`

## Results before evidence commit

1. Full suite
   - Command: `python3.13 -m pytest -q`
   - Result: `242 passed in 188.23s`
2. Focused cumulative contracts
   - Command: `python3.13 -m pytest -q tests/test_execution_policy.py tests/test_main_unit.py tests/test_final_gate.py tests/test_runner_brief.py`
   - Result: `81 passed in 134.70s`
3. Static checks
   - `git diff --check origin/main...HEAD`: pass
   - `python3.13 -m compileall -q codex_flow tests`: pass
   - conflict-marker and private-key/token signature scan: no actionable match

## Exact-final-HEAD rule

This report and `final-review.md` change the commit SHA. After this evidence is committed, the full suite and focused documentation/invariant tests must run again on that exact clean HEAD. A final gate, PR, CI or merge must not reuse the pre-evidence HEAD result as same-HEAD proof.
