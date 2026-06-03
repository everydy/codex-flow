---
name: codex-flow
description: "Compatibility alias for canonical `구현커밋`. Use for legacy `$codex-flow`, `Codex Flow`, `run-next`, `run-all`, `morning brief`, 상태, 브리핑, 리뷰, PR초안, PR생성, 병합, dashboard, PR lock, pr-check, and drain."
metadata:
  short-description: "Legacy codex-flow alias for 구현커밋"
---

# codex-flow

`codex-flow` is a compatibility alias for the canonical `구현커밋` skill.

When this skill is loaded, read and follow the first available canonical body:

- Repo mirror: `/Users/moonsoo/projects/codex-flow/skills/구현커밋/SKILL.md`
- Installed skill root, when present: `/Users/moonsoo/projects/codex-skills-user/구현커밋/SKILL.md`

## Alias Contract

- Treat `$codex-flow`, `Codex Flow`, `run-next`, `run-all`, and related legacy wording as equivalent to `구현커밋`.
- Keep old calls working, but lead new human-facing docs and summaries with `구현커밋`.
- Do not duplicate the workflow rules here. The canonical `구현커밋` skill owns routing, guardrails, auto-resolve behavior, review gates, and command mapping.
- Do not rename internal runtime surfaces during this migration: `codex_flow`, `.codex-flow`, and `/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py` remain compatibility names.
