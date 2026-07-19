# Managed Child Runtime Operational Contract

## Scope

This document records the operator-visible contract implemented by Commits 1–2. It does not replace attestation: managed preparation creates the exact closure that the existing fail-closed attestation verifies before branch, dirty-worktree, or implementer work.

## Runtime selection

| Input | Result |
| --- | --- |
| `CODEX_FLOW_CHILD_HOME` and `CODEX_FLOW_CHILD_MANIFEST` both unset | Build or reuse a managed exact closure. |
| Both variables set | Validate and reuse the explicit closure without mutation. |
| Only one variable set | Fail before edit with a precise pair-required error. |
| Explicit `--profile`/`-p` without a pair | Fail before edit; managed preparation cannot safely copy a profile. |
| Parent custom provider without a pair | Fail closed while sanitizing config. |
| `CODEX_FLOW_CHILD_ISOLATION=0` | Reject canonical managed execution; isolation is not a recovery bypass. |

The managed layout is:

```text
${CODEX_CHILD_RUNTIME_ROOT:-$CODEX_HOME/child-runtimes}/
  codex-flow/
    <repo-hash>/
      <closure-hash>/
        config.toml
        auth.json -> <parent CODEX_HOME>/auth.json  # only when parent auth exists
        skills/<required-standalone-id>/...
        plugins/...                                # only discovered child-owned payloads
        child-closure-manifest.json
```

`<repo-hash>` is a namespace, not a prepared runtime. Only the validated `<closure-hash>` directory plus its manifest is passed through attestation, implementation, and resumed review.

## Cache and invalidation

The closure hash binds:

- target repo identity and normalized required skill ids;
- copied standalone source tree hashes;
- child-owned plugin payload hashes and their skill ids;
- exact external skill ids observed by fresh inventory;
- sanitized config digest and manifest schema;
- Codex CLI version, command, and explicit extra args.

Any changed bound input selects a different cache key. A per-key thread lock and file lock protect unique private staging and atomic publish. An existing key is loaded and validated rather than overwritten.

## Isolation and secrets

- Runtime, staging, and lock directories are private (`0700`); generated config, manifest, and lock files are `0600`.
- Authentication is not copied into the closure. If parent `auth.json` exists, the child contains only a symlink to it.
- Required standalone sources inside the target repo and sources containing internal symlinks are rejected.
- Diagnostics never include auth tokens or config payloads.

## Diagnostics and no-edit boundary

The plan log uses these stable records:

```text
child_runtime event=prepare source=managed cache_key=<digest>
child_runtime event=reuse source=<managed|explicit> cache_key=<digest>
child_runtime event=deny reason=<exception-class>
```

On prepare or attestation denial, the unit becomes `needs_work`, `changed_paths=[]`, and the implementer is not launched. A successful attestation is written under `attestations/<unit>/<nonce>.json` and binds the child home, manifest, plan, CLI version, inventory, TTL, and nonce before editing begins.

## Operator commands

Managed default—do not inject a prepared pair:

```bash
env -u CODEX_FLOW_CHILD_HOME -u CODEX_FLOW_CHILD_MANIFEST \
  python3 scripts/codex_flow.py run-next --plan <plan.md>
```

Explicit override—always supply the pair:

```bash
CODEX_FLOW_CHILD_HOME=/absolute/prepared/home \
CODEX_FLOW_CHILD_MANIFEST=/absolute/prepared/home/child-closure-manifest.json \
  python3 scripts/codex_flow.py run-next --plan <plan.md>
```

See [raw-cli-proof.md](raw-cli-proof.md) for the disposable no-env execution evidence.
