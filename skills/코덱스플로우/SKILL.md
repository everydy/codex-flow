---
name: 코덱스플로우
description: "Compatibility alias for canonical `구현커밋`. Use when the user types $코덱스플로우, 상태, 라우트, 다음실행, 모두실행, 브리핑, 리뷰, PR초안, PR생성, 병합, PR체크, or 대시보드."
metadata:
  short-description: "$코덱스플로우 legacy alias for 구현커밋"
---

# 코덱스플로우

`코덱스플로우` is a Korean compatibility alias for the canonical `구현커밋` skill.

When this skill is loaded, read and follow the first available canonical body:

- Repo mirror: `/Users/moonsoo/projects/codex-flow/skills/구현커밋/SKILL.md`
- Installed skill root, when present: `/Users/moonsoo/projects/codex-skills-user/구현커밋/SKILL.md`

## Alias Contract

- Treat `$코덱스플로우`, `$코덱스플로우 상태`, `$코덱스플로우 라우트`, `$코덱스플로우 다음실행`, `$코덱스플로우 모두실행`, `$코덱스플로우 브리핑`, `$코덱스플로우 리뷰`, `$코덱스플로우 PR초안`, `$코덱스플로우 PR생성`, `$코덱스플로우 병합`, `$코덱스플로우 대시보드`, and `$코덱스플로우 PR체크` as equivalent to the matching `$구현커밋 ...` command.
- Keep old calls working, but lead new human-facing docs and summaries with `구현커밋`.
- Do not duplicate the workflow rules here. The canonical `구현커밋` skill owns routing, guardrails, auto-resolve behavior, review gates, and command mapping.
- Do not rename internal runtime surfaces during this migration: `codex_flow`, `.codex-flow`, and `/Users/moonsoo/projects/codex-flow/scripts/codex_flow.py` remain compatibility names.
