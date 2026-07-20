# Test Report — Implementation Commit Risk And Status Follow-up

## Mode

- canonical `테스트` packaging
- target: deterministic Python policy and CLI dashboard runtime
- local web server: not applicable
- deployment: not authorized
- fresh-agent virtual session: not run; the target is deterministic repository code rather than a prompt/skill routing contract, and direct function/CLI tests provide the stronger oracle.

## Verification Results

| Surface | Command | Result | Assertion |
| --- | --- | --- | --- |
| policy | `PYTHONPATH=. pytest tests/test_execution_policy.py -q` | 40 passed | false positives removed; true positives and safety floors preserved |
| dashboard/source integration | `PYTHONPATH=. pytest tests/test_crack_parity.py tests/test_route_plan_first_source.py -q` | 34 passed | main/isolated/fallback projection and existing integrations |
| combined post-review | `PYTHONPATH=. pytest tests/test_execution_policy.py tests/test_crack_parity.py tests/test_route_plan_first_source.py -q` | 75 passed | final edge-case and redaction matrix |
| full regression | `PYTHONPATH=. pytest -q` | 253 passed in 199.30s | repository regression suite before evidence-doc packaging |
| CLI smoke | `python3 scripts/codex_flow.py --repo /Users/moonsoo/projects/codex-flow dashboard` | pass | real active plans show owner, phase/status, elapsed, progress, wait, verification, status source |
| whitespace | `git diff --check main...HEAD` | pass | no whitespace errors |

## Final Same-HEAD Gate

After this report and the related planning artifacts are committed, rerun `PYTHONPATH=. pytest -q` on the exact packaged HEAD. The authoritative final same-HEAD result belongs in the final operator handoff because writing it back into this committed report would create a new HEAD.

## Manual Test

1. Run `python3 scripts/codex_flow.py --repo <repo> dashboard` in a repository with an active Codex Flow plan.
2. Confirm the active plan shows `Current owner`, `Current phase/status`, `Elapsed`, `Last progress`, `Waiting`, `Current verification`, and `Status source`.
3. For a main-direct unit without a ledger, confirm the source says queue fallback rather than inventing phase precision.
4. For an isolated attempt with heartbeat data, confirm phase and elapsed come from the attempt ledger.
5. Confirm long or token-like reasons are redacted/capped and dashboard rendering does not change queue or ledger files.

## Links

- Localhost: not applicable — CLI/runtime change.
- Existing deploy: not applicable / not discovered — no deploy surface is defined for this local Python CLI.
