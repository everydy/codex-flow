# Source Convergence Manifest

## Fixed sources

- `origin/main`: `5eb1643642d3909ede446c1256d7146f4ba191e5`
- verified isolated-adapter baseline: `b2db89e63fc5357b5379783e867f64d165fce739`
- main-first transaction: `0089580`
- final-only policy: `dfad52c`
- cumulative final gate: `27e9069`
- canonical skill baseline for later semantic sync: `383ea76a0e491d9ed007d724d7e555350b2d9d1e`

`git rev-list --left-right --count origin/main...27e9069` returned `0 50`, and the merge base is exact `origin/main`. There is no remote-only divergence to reconcile.

## Disposition

| Source range | Disposition | Reason |
| --- | --- | --- |
| `origin/main..b2db89e` | keep | Contains the accumulated plan-first, managed child, attempt ledger, supervisor, attestation, and isolated read-only reviewer safety base. |
| `d5bc344` | keep | Approved main-first convergence plan and review/strategy evidence. |
| `0089580` | keep | Adds explicit main-owned begin/complete/hold transaction and disables automatic repair by default. |
| `dfad52c` | keep | Makes final-only the ordinary review policy and removes the final-review implementation unit. |
| `27e9069` | keep | Requires an atomic exact-HEAD/plan/queue final gate before real PR or merge effects. |
| Historical default-child and automatic-repair behavior | superseded semantically | Later commits change the active defaults; their earlier commits remain in ancestry because dropping them would also discard the verified safety base. |
| Canonical `구현커밋/SKILL.md` byte sync | drop | Phase 8 performs a semantic merge from the canonical skill repository instead of copying the Codex Flow mirror. |
| Product deployment | hold | This program changes the orchestration runtime and skill contract; it has no authorized product deployment target. |

## Integration decision

Do not replay or squash the 50-commit linear history. Create the integration branch at the verified descendant HEAD, add only this provenance record and later canonical semantic-sync evidence, then run the cumulative review and canonical tests on the final exact HEAD before PR/merge.

## Expected PR surface

- Codex Flow runtime, tests, operational docs, and implementation-commit mirror accumulated since `origin/main`.
- Main-first transaction and optional isolated adapter.
- `final_only` ordinary review with explicit `per_unit` high-risk opt-in.
- Atomic finalization gate.
- No canonical skill repository files until the separate Phase 8 PR.
- No product deploy, credential, token, cookie, or user project payload.
