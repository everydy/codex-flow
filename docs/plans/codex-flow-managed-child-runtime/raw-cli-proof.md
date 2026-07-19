# Raw CLI Proof: Managed Child Without Prepared-Child Environment

## Result

On 2026-07-19, a disposable local Git repository ran the canonical `scripts/codex_flow.py run-next` path with both prepared-child variables explicitly removed. A local fake Codex CLI supplied deterministic app-server inventory and implementer/review output, so the proof required no network, account, model call, or remote mutation.

The retained reusable inputs are:

- [fixtures/source-plan.md](fixtures/source-plan.md)
- [fixtures/fake_codex.py](fixtures/fake_codex.py)

The disposable setup copied only `plan-first-implementation` and `review-all-in-one` into an isolated parent skill registry, initialized a local Git repo, and routed the fixture plan. The canonical execution command was:

```bash
env -u CODEX_FLOW_CHILD_HOME -u CODEX_FLOW_CHILD_MANIFEST \
  CODEX_HOME="$SMOKE_CODEX_HOME" \
  CODEX_CHILD_RUNTIME_ROOT="$SMOKE_RUNTIME_ROOT" \
  python3 scripts/codex_flow.py --repo "$SMOKE_REPO" run-next \
    --plan "$PLAN_PATH" \
    --no-branch --no-commit \
    --codex-command "$SMOKE_ROOT/fake_codex.py"
```

`CODEX_HOME` and `CODEX_CHILD_RUNTIME_ROOT` point only at disposable fixture directories; neither is a prepared-child override.

Observed CLI result:

```text
unit: unit-001
action: done
changed_paths: fixture/work.txt
```

Observed plan log sequence:

```text
child_runtime event=prepare source=managed cache_key=<closure-hash>
Started commit unit 1: Execute a local fixture on codex/raw-cli-smoke.
Review gate for commit unit 1 attempt 0: review_gate=pass score=100 blockers=0 important=0 minor=0
done unit-001 | changed: fixture/work.txt | summary: managed child executed fixture | review_gate=pass score=100 blockers=0 important=0 minor=0
Completed commit unit 1.
```

The attestation JSON recorded:

- a child home at `.../child-runtimes/codex-flow/<repo-hash>/<closure-hash>`;
- loaded skills exactly `plan-first-implementation` and `review-all-in-one`;
- CLI version `codex-cli smoke-1.0`;
- non-empty manifest, plan, runtime-tree, and plugin-tree SHA-256 bindings;
- discovery command `<fake-codex> app-server --listen stdio://`.

Filesystem inspection showed the repo namespace and closure directory at mode `0700` and `child-closure-manifest.json` at mode `0600`. The marker contained exactly `managed child executed`, proving the fixture implementer ran after managed preparation and attestation. No legacy missing-home diagnostic appeared.

## Reproduction notes

Use a fresh `mktemp -d` root, copy the two fixture files above, copy the two required skills into `$SMOKE_CODEX_HOME/skills/`, initialize and commit the disposable source repo, then run `route` followed by the command above. Keep the target repo and runtime root outside this repository. The route step uses a disposable `--worktree-root`; no remote PR, merge, deploy, or external post is involved.
