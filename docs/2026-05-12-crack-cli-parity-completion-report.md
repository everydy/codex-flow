# Crack-CLI Agent Parity Completion Report

## Summary

Codex Flow has been upgraded from a local ticket/queue helper into a Python-first Crack-CLI-inspired orchestration layer with role-separated agents.

Implemented:

- Shared Codex CLI execution wrapper with default model/reasoning args.
- Markdown `plan.md` + `log.md` readiness parser.
- Router, Planner, Implementer, and Merge agent modules.
- Implementer two-pass flow: implementation call, then same-session review call.
- Structured inbox queue and drain-all behavior.
- Plan/log-aware PR readiness and PR dry-run generation.
- Merge runner with local merge, remote merge scaffolding, and merge-conflict agent hook.
- RunAll runner that can finalize to local branch, PR dry-run, remote PR, or merge.
- Dashboard and brief updates based on plan/log progress.
- README and Codex skill docs updated to reflect agent mode and safety defaults.

## Deliberate Differences From Crack-CLI

- The project remains Python-first rather than TypeScript.
- `.codex-flow/` remains the state directory rather than `.crack/`.
- Public CLI defaults remain conservative:
  - routing uses heuristic/template mode unless `--router codex --planner codex` is selected
  - implementation requires `--execute`
  - commits require `--commit`
  - remote PR/merge require explicit remote flags
- `queue.json` remains as a compatibility cache, while `plan.md` and `log.md` now drive readiness.

## Verification

```bash
python3 -m pytest
```

Result:

- 34 tests passed.

Additional verification required before public push:

- CLI help smoke checks.
- `git diff --check`.
- privacy scan for local paths, credentials, and personal terms.
