# Managed Child Runtime Raw CLI Smoke

## Skill Routing Manifest

| Phase | Required skills | Optional skills | Evidence |
| --- | --- | --- | --- |
| Commit 1: Execute a local fixture | `plan-first-implementation` | `없음` | `fixture/work.txt` proves implementation ran after attestation. |

## Implementation Plan

### Commit 1: Execute a local fixture

- target files: `fixture/work.txt`
- changes: write one deterministic marker through the fake Codex implementer.
- verification: confirm `fixture/work.txt` and the child runtime log event.
- success criteria: the unit finishes without caller-provided prepared-child variables.
- stop conditions: preparation or attestation fails, or execution edits another path.
