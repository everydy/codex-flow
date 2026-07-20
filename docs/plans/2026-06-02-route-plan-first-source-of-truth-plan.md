# 2026-06-02 | Route As Plan-First Execution Transformer | codex-flow-route-plan-first

## Problem

Codex Flow의 현재 `route`는 자연어 요청을 받아 ticket을 만들고, 그 ticket을 `inbox`, 기존 plan, 새 plan 중 하나로 보내는 라우팅 기능이다.

현재 기본 흐름은 아래에 가깝다.

```text
user request -> ticket -> router decision -> existing plan / inbox / new plan -> queue
```

이 구조는 짧은 요청을 작업 큐로 보내기에는 좋지만, 이미 `plan-first-implementation`으로 충분히 작성된 계획 문서가 있을 때는 원본 계획을 다시 작게 해석하면서 정보가 줄거나 바뀔 수 있다.

이번 변경의 목표는 Codex Flow를 "계획 생성기"보다 "승인된 plan-first 문서를 실행 가능한 ticket/macro-plan/queue 구조로 바꾸는 실행 스킬"에 가깝게 만드는 것이다.

## Context

### Conversation Context

- 사용자는 `$코덱스플로우 <요청>`을 기본적으로 `route "<요청>" --auto-resolve`로 해석하도록 스킬 문서를 바꿨다.
- route smoke test는 임시 git repo에서 통과했다.
  - `route "플랜대로 실행" --router heuristic --planner template --auto-resolve`
  - ticket 1개, plan 1개, ready unit 3개 생성
  - `Skill Routing Manifest` 포함 확인
- 사용자는 이후 Codex Flow의 진짜 방향을 "이미 plan-first로 열심히 짠 문서를 보고 실행 ticket과 거시 plan을 붙이는 스킬"로 잡았다.
- 사용자는 별도 `adopt-plan` 모드보다, 앞으로 `route` 자체를 plan-first 문서 기준으로 바꾸는 방향이 맞는지 물었다.
- 결론: 사용자의 전제가 "route는 앞으로 전부 plan-first 문서 기준"이라면, 새 모드보다 `route`의 정체성을 바꾸는 것이 더 일관적이다.
- 후속 검토 결론: 기존 자연어 route 자동 선택 기능과 사용자-facing `submit` raw request 저장 기능을 제거한다. Codex Flow 실행 입구는 plan-first Markdown 문서로 좁힌다.

### Current Route Role

현재 route의 역할은 아래다.

```text
natural-language request -> submit ticket -> router decides destination -> plan creation or append
```

구현상 핵심 파일은 다음과 같다.

- `codex_flow/cli.py`
  - `route`와 `submit`이 같은 `add_ticket_args(...)`를 공유한다.
  - `route`는 항상 `tickets.submit_ticket(...)`을 먼저 호출한다.
  - PR lock이면 inbox로 보낸다.
  - `--plan`이 있으면 기존 plan의 `requests.md`에 붙인다.
  - 아니면 `route_decision(...)`으로 `existing_plan`, `new_plan`, `pause_for_pr_review` 중 하나를 고른다.
  - `new_plan`이면 `plans.create_plan_from_ticket(...)`을 호출한다.
- `codex_flow/router_agent.py`
  - Router는 "PR lock이면 pause, active plan에 의존하면 existing_plan, 아니면 new_plan"을 고른다.
  - 활성 plan의 `plan.md`와 queue를 읽어 판단한다.
- `codex_flow/plans.py`
  - `create_plan_from_ticket(...)`은 ticket title을 바탕으로 plan directory, `plan.md`, `queue.json`, `queue.md`, `requests.md`, `log.md`를 만든다.
  - `append_plan_request(...)`는 기존 plan에 후속 요청을 붙인다.
  - `ensure_plan_skill_routing_manifest(...)`는 plan에 `Skill Routing Manifest`를 보강한다.
- `codex_flow/runner.py`
  - 실행 단계에서는 이미 `plan.md`를 source of truth로 읽는 방향이 강하다.
  - implementer prompt에 `Read this plan as the source of truth` 문구가 들어간다.

### Current Code Evidence

현재 `route`가 자연어 요청을 먼저 ticket으로 만드는 근거:

```py
if args.command in {"submit", "route"}:
    ticket = tickets.submit_ticket(
        args.title,
        repo=args.repo,
        priority=args.priority,
        project=args.project,
        allow_draft_pr=args.allow_draft_pr,
        no_implement=args.no_implement,
    )
    print(f"ticket_created: {ticket.path}")
```

현재 router가 기존 plan/new plan을 고르는 근거:

```py
def route_decision(args: argparse.Namespace, prompt: str, lock: Path | None) -> RouterAgentDecision:
    if args.branch or args.plan_title:
        title = args.plan_title or prompt.splitlines()[0][:80] or "User Request"
        return RouterAgentDecision("new_plan", args.reason, branch_name=args.branch or f"codex/{state.slugify(title)}", plan_title=title)
    repo = state.resolve_repo(args.repo)
    lock_text = lock.read_text(encoding="utf-8") if lock else None
    active_plans = collect_active_plans(repo)
    agent = CodexRouterAgent(command=args.codex_command, extra_args=args.codex_arg) if args.router == "codex" else HeuristicRouterAgent()
    return agent.decide(RouterAgentInput(repo=repo, prompt=prompt, pr_lock=lock_text, active_plans=active_plans))
```

현재 runner가 `plan.md`를 source of truth로 읽는 근거:

```py
"## Full Plan Context",
"",
"Read this plan as the source of truth for the selected commit unit. Do not rely only on the generic unit title.",
"",
"```md",
plan_content.strip() or "No plan.md content found.",
"```",
```

이 근거 때문에 새 route는 runner 전체를 갈아엎기보다, 앞단에서 `plan-first source -> Codex Flow plan directory` 변환을 정확히 만드는 것이 우선이다.

### Why Route Existed

기존 route가 필요했던 이유는 "여러 작업 상태 중 어디에 넣을지 자동 결정"하기 위해서였다.

route가 없으면 사용자가 매번 아래를 직접 판단해야 한다.

- 지금 PR lock이 있어서 inbox로 보내야 하는가
- 기존 active plan에 붙여야 하는가
- 새 plan을 만들어야 하는가
- 어떤 branch/title을 써야 하는가

즉 기존 route는 실행 엔진이라기보다 작업 배치기였다.

## Goal

`route`를 다음 의미로 재정의한다.

```text
Codex Flow route는 자연어 요청을 새 plan으로 바꾸는 기능이 아니라,
plan-first 원본 문서를 실행 가능한 ticket/macro-plan/queue 구조로 변환하는 기능이다.
```

새 route의 기본 흐름은 아래다.

```text
plan-first source -> source snapshot -> ticket extraction -> macro execution plan -> queue/commit units -> run-next/run-all
```

## Non-Goals

- 이번 계획은 바로 구현하지 않는다.
- remote PR 생성, merge, deploy, production 작업은 포함하지 않는다.
- `run-next`와 `run-all`의 Codex implementer 실행 방식 자체를 갈아엎지 않는다.
- 모든 기존 route behavior를 보존하려고 별도 `adopt-plan` 모드를 만들지 않는다.
- `route "짧은 요청"`을 자동 혼합 fallback으로 유지하지 않는다.
- plan-first 문서를 LLM으로 완벽하게 재작성하는 기능을 만들지 않는다. 원본 보존과 추출이 목표다.

## Proposed Route Contract

### New Meaning

`route`는 plan-first 문서를 실행 대상으로 채택하고, 그 문서에서 ticket과 macro plan을 만든다.

명령 예시는 다음 중 하나로 정리한다.

```bash
python3 scripts/codex_flow.py route docs/plans/example-plan.md --auto-resolve
python3 scripts/codex_flow.py route "docs/plans/example-plan.md" --auto-resolve
```

스킬 호출 예시는 다음이다.

```text
$코덱스플로우 docs/plans/example-plan.md
```

### Input Resolution

route 입력은 먼저 plan-first source로 해석한다. 여기서 CLI와 Codex skill wrapper의 책임을 분리한다.

1. CLI 입력이 기존 파일 경로이고 Markdown이면 그 파일을 source plan으로 채택한다.
2. `$코덱스플로우` skill wrapper에서는 현재 요청이나 직전 대화 맥락에 plan-first 문서 링크/경로가 명시되어 있으면 그 문서를 source plan으로 채택할 수 있다.
3. 현재 요청이 "플랜대로 실행"처럼 짧아도, 직전 대화 턴에서 단일 plan-first 문서를 찾을 수 있으면 그 문서 경로로 route한다.
4. plan-first 문서를 언급하지 않았고, 대화 맥락에서도 단일 문서를 찾을 수 없거나 후보가 여러 개면 실행하지 않는다.
5. 기존 자연어 route는 제거한다. `route`는 plan-first Markdown 문서 source가 확인된 경우에만 실행한다.

추천 기본값은 다음이다.

```text
$코덱스플로우 <input>:
  if input has markdown plan path/link -> route that plan-first source
  else if previous/current conversation has exactly one findable plan-first document -> route that source
  else -> stop and ask for the plan-first document or Markdown path

CLI route <input>:
  if input is markdown file path -> plan-first source route
  else -> fail with guidance, do not create generic plan from short request
```

이 기본값은 사용자의 "앞으로 라우트는 전부 플랜 문서대로만"이라는 방향과 맞다.

### Remove Public Submit

사용자는 `submit`을 쓸 일이 없다고 명확히 밝혔다. 따라서 `submit`을 route에서 분리해 남기는 것이 아니라, 사용자-facing raw request capture 기능 자체를 제거 대상으로 둔다.

```text
route docs/plans/foo.md -> plan-first source 채택
route "짧은 요청" -> 실패 안내
submit "짧은 요청" -> 지원하지 않음 또는 제거됨
```

이 제거는 내부 정합성에 중요하다.

- `route`는 plan-first 실행 전담으로 명확해진다.
- 기존처럼 짧은 요청을 로컬 inbox에 남기는 기능도 제거된다.
- 계획문서가 아닌 입력은 Codex Flow 실행 대상이 아니다.
- 다만 `$코덱스플로우 플랜대로 실행`처럼 짧은 입력이라도 직전 대화 턴에서 단일 plan-first 문서를 찾을 수 있으면 그 문서로 route할 수 있다.
- plan-first 문서를 찾을 수 없거나 후보가 애매하면 "plan-first 문서를 말해주거나 Markdown 경로를 넘겨 달라"고 묻고 멈춘다.
- `route`가 다시 `submit_ticket(...)`을 먼저 호출하는 구조를 끊을 수 있다.
- `tickets.submit_ticket(...)` 같은 Python helper는 내부 adoption ticket 작성용으로 남길 수 있지만, CLI `submit` command는 제거한다.

## New State Model

### Source Plan Snapshot

Codex Flow plan directory 안에 원본 plan-first 문서 정보를 저장한다.

예상 파일:

```text
.codex-flow/plans/<slug>/
  source-plan.md
  source.json
  macro-plan.md
  tickets/
  plan.md
  queue.json
  queue.md
  requests.md
  log.md
  decisions.md
  artifacts.md
  handoff.md
```

`source.json` 예시:

```json
{
  "source_path": "docs/plans/example-plan.md",
  "source_sha256": "...",
  "source_title": "2026-06-02 | Example Plan | example-plan",
  "adopted_at": "2026-06-02T00:00:00",
  "route_mode": "plan_first_source",
  "extraction_confidence": "high"
}
```

`source-plan.md`는 실행 시작 순간의 source 문서 사본이다. 원본 `docs/plans/example-plan.md`는 route 중에 수정하지 않는다.

### Ticket Extraction

기존 ticket은 "사용자 요청 하나"였지만, 새 route에서는 plan-first 문서에서 실행 가능한 ticket들을 뽑는다.

ticket 후보는 아래 기준으로 나눈다.

- 명확한 구현 phase
- 독립 검증 가능한 작업 단위
- 서로 다른 owner 또는 surface
- hard stop 전 단계
- final gate 또는 QA gate

예상 ticket 파일:

```text
.codex-flow/plans/<slug>/tickets/ticket-001.md
.codex-flow/plans/<slug>/tickets/ticket-002.md
.codex-flow/plans/<slug>/tickets/ticket-003.md
```

기존 `.codex-flow/tickets/*.md`는 전체 source plan을 가리키는 top-level ticket으로 유지한다.

추천:

- top-level ticket: source plan 채택 기록
- plan-local tickets: 실행 단위

### Macro Plan

`macro-plan.md`는 ticket 간 순서와 의존 관계를 보여준다.

필수 내용:

- source plan 링크
- extracted tickets
- ticket dependency graph
- execution phases
- stop/revise conditions
- final gate

### Queue Mapping

기존 `queue.json`은 commit unit 중심이다. 새 구조에서는 ticket과 commit unit이 연결되어야 한다.

예상 queue unit 필드 추가:

```json
{
  "id": "unit-001",
  "ticket_id": "ticket-001",
  "source_plan_ref": {
    "path": "docs/plans/example-plan.md",
    "section": "## Implementation Steps",
    "excerpt_hash": "..."
  },
  "title": "...",
  "status": "ready"
}
```

## Detailed Implementation Design

### CLI Contract Narrowing

현재 `submit`과 `route`가 같은 `add_ticket_args(...)`를 공유하므로 route 전환 시 public raw request entry를 제거해야 한다.

CLI 목표 구조:

```py
def add_route_source_args(command: argparse.ArgumentParser) -> None:
    command.add_argument("source_plan", type=Path)
    command.add_argument("--auto-resolve", action="store_true")
    command.add_argument("--branch")
    command.add_argument("--title", dest="plan_title")
```

Skill wrapper 목표 구조:

```py
def resolve_skill_route_source(current_request: str, conversation_context: list[str]) -> Path | None:
    candidates = find_markdown_plan_links(current_request)
    if not candidates:
        candidates = find_markdown_plan_links("\n".join(recent_plan_first_turns(conversation_context)))
    if len(candidates) == 1:
        return candidates[0]
    return None
```

Skill wrapper behavior:

```text
if exactly one plan-first Markdown source is found:
  run CLI route <that-path> --auto-resolve
else:
  do not call CLI
  ask the user to provide the plan-first document or Markdown path
```

route command 처리 목표:

```py
if args.command == "route":
    source = source_plan.resolve_source_plan(args.source_plan, repo=args.repo)
    lock = pr.read_pr_lock(args.repo)
    if lock:
        repo = state.resolve_repo(args.repo)
        inbox.append_inbox_request(repo, str(source.path), "PR lock is active; source plan route deferred.")
        print("route: queued source plan due to active PR lock")
        return 0
    plan = plans.create_plan_from_source(
        source,
        repo=args.repo,
        branch_name=args.branch,
        plan_title=args.plan_title,
        prepare_git_branch=True,
    )
    print(f"source_plan_adopted: {plan.directory / 'source-plan.md'}")
    print(f"plan_created: {plan.plan_path}")
    print(f"queue_created: {plan.queue_md}")
    return 0
```

Non-file route input failure:

```py
def resolve_source_plan(value: str | Path, repo: str | Path | None = None) -> SourcePlan:
    repo_path = state.resolve_repo(repo)
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = repo_path / candidate
    if not candidate.exists() or candidate.suffix.lower() not in {".md", ".markdown"}:
        raise SystemExit(
            "route requires a plan-first Markdown file path. "
            "Create a plan-first document first, then pass its Markdown path to route."
        )
    return SourcePlan(path=candidate.resolve(), repo=repo_path)
```

### Source Plan Module

새 모듈 `codex_flow/source_plan.py`를 만든다.

필수 책임:

- source path 해석
- Markdown 여부 검증
- sha256 계산
- title 추출
- snapshot 쓰기
- drift 비교

코드 스니펫:

```py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json

from . import state


@dataclass(frozen=True)
class SourcePlan:
    path: Path
    repo: Path
    content: str
    sha256: str
    title: str


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def title_from_markdown(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or fallback
    return fallback


def snapshot_source_plan(source: SourcePlan, plan_dir: Path) -> None:
    (plan_dir / "source-plan.md").write_text(source.content, encoding="utf-8")
    metadata = {
        "source_path": str(source.path.relative_to(source.repo) if source.path.is_relative_to(source.repo) else source.path),
        "source_sha256": source.sha256,
        "source_title": source.title,
        "adopted_at": state.timestamp(),
        "route_mode": "plan_first_source",
    }
    (plan_dir / "source.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
```

Implementation note: `Path.is_relative_to(...)` is available in current supported Python. If compatibility with older Python is needed, replace it with a `try: relative_to(...) except ValueError` helper.

### Plan-First Extraction Module

새 모듈 `codex_flow/plan_first_extract.py`를 만든다.

초기 버전은 Markdown parser dependency를 추가하지 않고 heading 기반으로 시작한다.

추출 우선순위:

1. `### Commit N:` headings
2. `## Implementation Plan` 아래의 `### Commit ...` headings
3. numbered phase headings such as `### Phase 1`
4. fallback: whole source plan as one low-confidence ticket

코드 스니펫:

```py
@dataclass(frozen=True)
class ExtractedTicket:
    id: str
    title: str
    source_section: str
    excerpt: str
    confidence: str


COMMIT_RE = re.compile(r"^###\s+Commit\s+(\d+):\s*(.+?)\s*$", re.MULTILINE)


def extract_tickets(source_content: str) -> list[ExtractedTicket]:
    matches = list(COMMIT_RE.finditer(source_content))
    if not matches:
        return [
            ExtractedTicket(
                id="ticket-001",
                title="Execute source plan",
                source_section="#",
                excerpt=source_content[:2400],
                confidence="low",
            )
        ]
    tickets: list[ExtractedTicket] = []
    for index, match in enumerate(matches, start=1):
        start = match.start()
        end = matches[index].start() if index < len(matches) else len(source_content)
        tickets.append(
            ExtractedTicket(
                id=f"ticket-{index:03d}",
                title=match.group(2).strip(),
                source_section=match.group(0).strip(),
                excerpt=source_content[start:end].strip(),
                confidence="high",
            )
        )
    return tickets
```

### Plan Creation From Source

`plans.create_plan_from_ticket(...)`는 유지하고, 새 함수 `plans.create_plan_from_source(...)`를 추가한다.

목표:

- 기존 ticket 기반 plan 생성은 public `submit` command가 아니라 internal helper 또는 legacy-only plan command에서만 유지
- source 기반 route는 새 함수로 분리
- `write_plan_files(...)`를 재사용하되 source metadata와 extracted tickets를 queue data에 추가

코드 스니펫:

```py
def create_plan_from_source(
    source: source_plan.SourcePlan,
    repo: str | Path | None = None,
    branch_name: str | None = None,
    plan_title: str | None = None,
    prepare_git_branch: bool = False,
) -> Plan:
    flow = state.ensure_initialized(repo)
    title = plan_title or source.title
    slug = unique_slug(title, flow.plans)
    branch = branch_name or f"codex/{slug}"
    if prepare_git_branch and is_git_repo(flow.repo):
        prepare_branch(flow.repo, branch)
    plan_dir = flow.plans / slug
    plan_dir.mkdir(parents=True, exist_ok=False)
    (plan_dir / "prompts").mkdir(parents=True, exist_ok=True)
    (plan_dir / "tickets").mkdir(parents=True, exist_ok=True)

    top_level_ticket = tickets.create_internal_ticket(
        f"Adopt source plan: {title}",
        repo=flow.repo,
        project="codex-flow",
        no_implement=True,
    )
    extracted = plan_first_extract.extract_tickets(source.content)
    source_plan.snapshot_source_plan(source, plan_dir)
    plan_first_extract.write_ticket_files(plan_dir / "tickets", extracted)

    queue_data = queue_from_source_tickets(source, extracted, slug, title, branch, top_level_ticket)
    plan = Plan(slug, plan_dir, plan_dir / "plan.md", plan_dir / "queue.json", plan_dir / "queue.md")
    write_source_plan_files(plan, source, extracted, queue_data)
    ensure_plan_skill_routing_manifest(plan.plan_path, queue_data)
    plan_readiness.sync_queue_cache_from_plan(plan.plan_path)
    tickets.update_ticket_status(top_level_ticket.path, "planned")
    state.refresh_dashboard(flow.repo)
    return plan
```

### Runner Prompt Extension

`runner.render_prompt(...)` already includes `plan.md`. New source mode must add source snapshot and selected ticket context, not replace existing plan context.

코드 스니펫:

```py
def read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


source_plan_content = read_optional(plan_dir / "source-plan.md")
macro_plan_content = read_optional(plan_dir / "macro-plan.md")
ticket_path = plan_dir / "tickets" / f"{unit.get('ticket_id', '')}.md"
ticket_content = read_optional(ticket_path)
```

Prompt section target:

```py
"## Source Plan Snapshot",
"",
"This is the original plan-first document adopted by route. Treat it as the upstream source.",
"",
"```md",
source_plan_content.strip() or "No source-plan.md content found.",
"```",
"",
"## Macro Plan Context",
"",
"```md",
macro_plan_content.strip() or "No macro-plan.md content found.",
"```",
"",
"## Selected Ticket",
"",
"```md",
ticket_content.strip() or "No plan-local ticket found for this unit.",
"```",
```

### Drift Guard

Source drift means the original plan-first file changed after route adopted it.

Default behavior:

- `dashboard`: warning only
- `review`: warning only
- `run-next`: hard stop unless `--accept-source-drift`
- `run-all`: hard stop unless `--accept-source-drift`

코드 스니펫:

```py
def check_source_drift(plan_dir: Path) -> SourceDrift:
    metadata_path = plan_dir / "source.json"
    if not metadata_path.exists():
        return SourceDrift(False, "no source metadata")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    source_path = Path(metadata["source_path"])
    if not source_path.is_absolute():
        source_path = plan_dir.parents[2] / source_path
    if not source_path.exists():
        return SourceDrift(True, "source file missing")
    current = sha256_text(source_path.read_text(encoding="utf-8"))
    return SourceDrift(current != metadata["source_sha256"], "source changed" if current != metadata["source_sha256"] else "clean")
```

## Implementation Plan

### Commit 1: Route Contract And Documentation

Target files:

- `README.md`
- `skills/codex-flow/SKILL.md`
- `skills/코덱스플로우/SKILL.md`
- `/Users/moonsoo/projects/codex-skills-user/codex-flow/SKILL.md`
- `/Users/moonsoo/projects/codex-skills-user/코덱스플로우/SKILL.md`

Changes:

- Redefine route as plan-first source route.
- Explain that short natural-language route is no longer the default.
- Document that old natural-language route behavior is removed.
- Add examples for `$코덱스플로우 docs/plans/foo.md`.
- Add skill-wrapper resolution rule:
  - use explicit plan-first Markdown path/link in the current request
  - otherwise use exactly one findable plan-first document from recent conversation context
  - if none or ambiguous, ask the user for the plan-first document/path and stop
- Update skill descriptions that still say `라우트는 inbox/existing-plan/new-plan으로 요청을 보낸다`.

Verification:

```bash
rg -n "natural-language|inbox|existing_plan|new_plan|plan-first|source plan|from-request" README.md skills /Users/moonsoo/projects/codex-skills-user/codex-flow/SKILL.md /Users/moonsoo/projects/codex-skills-user/코덱스플로우/SKILL.md
```

Expected assertions:

- README no longer describes `route` as natural-language request routing.
- `submit` is no longer documented as a normal user command.
- `$코덱스플로우 <문서경로>` maps to `route <문서경로> --auto-resolve`.
- `$코덱스플로우 플랜대로 실행` is allowed only when the skill wrapper can identify exactly one recent plan-first document.
- `$코덱스플로우 플랜대로 실행` without a findable plan-first document stops with a request for the document/path.

### Commit 2: CLI Input Contract

Target files:

- `codex_flow/cli.py`
- `tests/test_crack_parity.py`
- `tests/test_runner_brief.py`
- new test file: `tests/test_route_plan_first_source.py`

Changes:

- Remove the public `submit` command.
- Keep ticket-writing helpers only for internal adoption records if needed.
- Replace shared `add_ticket_args(...)` with route-only source args.
- Add route source resolver.
- Detect Markdown file paths.
- Make file-path route use plan-first source mode.
- Make non-file route input fail with clear guidance.
- Keep conversation-context plan lookup in skill docs/wrapper behavior, not in the CLI parser.
- Remove route dependency on `route_decision(...)` for normal route.
- Do not add `--from-request`; route is now dedicated to plan-first source documents.

Tests to update:

- `test_route_reuses_single_active_plan` should be removed or rewritten as a non-file route failure test.
- `test_route_explicit_plan_appends_request` should be removed from route tests. If plan-local follow-up requests are still needed, cover them through a separate helper, not `route`.
- `test_route_queues_when_pr_lock_is_active` must change: route should validate source document first; PR lock should block source plan adoption only after valid source input.
- Add parser assertion that `submit` is no longer an available public subcommand.

Verification:

```bash
python3 -m pytest tests/test_crack_parity.py tests/test_runner_brief.py tests/test_route_plan_first_source.py
```

### Commit 3: Source Plan Snapshot And Metadata

Target files:

- new module: `codex_flow/source_plan.py`
- `codex_flow/plans.py`
- tests: `tests/test_route_plan_first_source.py`

Changes:

- Resolve source plan path relative to repo.
- Reject non-existing files and non-Markdown files.
- Copy source plan into `.codex-flow/plans/<slug>/source-plan.md`.
- Write `source.json` with source path, hash, title, route mode, adopted timestamp.
- Preserve original source file. Do not rewrite it during route.

Verification:

```bash
python3 -m pytest tests/test_route_plan_first_source.py
```

Assertions:

- `source-plan.md` exists.
- `source.json` has `source_path`, `source_sha256`, `source_title`, `route_mode`.
- Original plan file is unchanged.
- `route "not a file"` exits non-zero and does not create ticket or plan.

### Commit 4: Ticket Extraction And Macro Plan

Target files:

- new module: `codex_flow/plan_first_extract.py`
- `codex_flow/plans.py`
- tests: `tests/test_route_plan_first_source.py`

Changes:

- Parse source plan headings and required sections.
- Extract ticket candidates.
- Write plan-local `tickets/ticket-*.md`.
- Write `macro-plan.md`.
- Add fallback behavior when extraction is uncertain:
  - create one ticket for the whole source plan
  - mark extraction confidence low
  - require review before `run-all --auto-resolve`

Verification:

```bash
python3 -m pytest tests/test_route_plan_first_source.py
```

Assertions:

- plan with 3 `### Commit N:` sections creates 3 tickets.
- macro-plan lists ticket order.
- uncertain plan creates one ticket and review warning.
- low-confidence extraction adds a queue-level warning field.

### Commit 5: Queue Generation From Source Tickets

Target files:

- `codex_flow/plans.py`
- `codex_flow/plan_readiness.py`
- `codex_flow/runner.py`
- tests:
  - `tests/test_route_plan_first_source.py`
  - `tests/test_runner_brief.py`

Changes:

- Generate queue units from extracted tickets.
- Add `ticket_id`, `source_plan_ref`, and `extraction_confidence` to queue units.
- Ensure `plan_readiness.sync_queue_cache_from_plan(...)` preserves source fields when it rebuilds queue units from `plan.md`.
- Ensure implementer prompt includes:
  - full source plan snapshot
  - selected ticket
  - selected commit unit
  - macro-plan context
- Keep existing `Skill Routing Manifest` behavior, but derive it from extracted tickets when possible.

Verification:

```bash
python3 -m pytest tests/test_route_plan_first_source.py tests/test_runner_brief.py
```

Assertions:

- run-next prompt includes source plan snapshot.
- run-next prompt includes selected ticket.
- queue unit links to source section.
- existing `Read this plan as the source of truth` prompt assertion still passes.

### Commit 6: Sync And Drift Checks

Target files:

- `codex_flow/source_plan.py`
- `codex_flow/dashboard.py`
- `codex_flow/briefs.py`
- `codex_flow/runner.py`
- `codex_flow/run_all.py`
- `codex_flow/cli.py`
- tests: `tests/test_route_plan_first_source.py`

Changes:

- Compare current source file hash against `source.json`.
- Dashboard shows source drift if original plan changed after route.
- Review brief includes source drift warning.
- Add `--accept-source-drift` to `run-next` and `run-all`.
- `run-next/run-all` hard stop if source drift exists and override is absent.

Recommended default:

- `run-next`: hard stop on source drift unless `--accept-source-drift`.
- `run-all`: hard stop on source drift unless `--accept-source-drift`.
- `dashboard`: show warning only.
- `review`: include warning.

Verification:

```bash
python3 -m pytest tests/test_route_plan_first_source.py tests/test_runner_brief.py
```

Assertions:

- dashboard contains source drift warning after source file edit.
- review artifact contains source drift warning.
- run-next exits non-zero on drift without override.
- run-next continues with `--accept-source-drift`.

### Commit 7: Remove Legacy Route Behavior

Target files:

- `codex_flow/cli.py`
- `codex_flow/tickets.py`
- `codex_flow/router_agent.py`
- `README.md`
- `skills/codex-flow/SKILL.md`
- `skills/코덱스플로우/SKILL.md`
- tests

Decision locked:

- Remove short natural-language route behavior.
- `route "짧은 요청"` should fail with guidance to provide a plan-first Markdown document.
- Do not keep `--from-request`.
- Remove public `submit` raw request capture behavior.
- Keep `router_agent.py` only if another command still uses it. If no caller remains, remove it with its tests.
- Keep low-level ticket helpers only if source-plan adoption records need them. They must not expose a general user command for short requests.

Verification:

```bash
rg -n "route_decision|RouterAgent|HeuristicRouterAgent|CodexRouterAgent|existing_plan|new_plan|pause_for_pr_review" codex_flow tests README.md skills
```

Expected assertions:

- No route code path calls `route_decision(...)`.
- Any remaining router agent reference is either removed or explicitly documented as non-route legacy/internal behavior.

## Review Iteration Log

### Cycle 1: Pre-Reinforcement Review

Findings:

- Important: the plan said "remove legacy route" but still described compatibility risk without naming exact tests and code branches to update.
- Important: the plan did not separate `submit` and `route` parser args, even though current code shares `add_ticket_args(...)`.
- Important: later user feedback clarified that `submit` is not needed either. Keeping it would preserve an input path the user does not want.
- Important: source drift was mentioned but not attached to exact commands or flags.
- Important: the first reinforced draft still left old `--router/--planner` route verification and did not show PR lock handling in the new route path.
- Minor: `source.json` did not include extraction confidence, making low-confidence extraction harder to show in dashboard/review.
- Minor: code snippets were missing, so another Codex would still need to infer the implementation shape.

Resolution in this revision:

- Added CLI split design and snippets.
- Updated the CLI direction again from `submit` separation to public `submit` removal.
- Added source plan, extraction, plan creation, runner prompt, and drift guard snippets.
- Added test rewrite notes for existing route tests.
- Removed legacy `--router/--planner` from new route verification and added PR lock defer behavior for source routes.
- Added top-level adoption ticket creation inside `create_plan_from_source(...)`.
- Changed Operator decision section from open decision to locked default.

### Cycle 2: Post-Reinforcement Review

Review criteria:

- Does the plan still claim route accepts natural-language input? No. The plan consistently says non-file route input fails.
- Does the plan allow a short request only when a plan-first document is findable from conversation context? Yes. That resolution happens in the skill wrapper, not in the CLI.
- Does the plan preserve a raw ticket user path? No. Public `submit` is removed because the user said it is unnecessary.
- Does the plan preserve source-plan route behavior under active PR lock? Yes. Valid source input is queued as a deferred source route instead of creating a new plan while locked.
- Does the plan identify code areas that will lose old assumptions? Yes. `cli.py`, `tests/test_crack_parity.py`, `tests/test_runner_brief.py`, README, skills, and `router_agent.py` are named.
- Does the plan explain how source context reaches implementation? Yes. `source-plan.md`, `macro-plan.md`, selected ticket, and `plan.md` prompt sections are specified.
- Does the plan specify stop conditions? Yes. source drift and low-confidence extraction are hard stops or review gates.
- Does the plan still use old route router/planner flags as a new verification command? No. Those remain only in historical context and removal checks.

Remaining review result:

- 발견된 blocker 없음.
- 발견된 important 문서 정합성 문제 없음.
- 남은 risk는 구현 중 실제 test failure로 확인해야 하는 영역이며, 계획 문서 안의 `Open Risks`와 `Verification Commands`에 남긴다.

## Operator 결정 필요 사항

- 상태: 없음
- Codex가 선택한 기본값:
  - `route`는 plan-first Markdown 문서 경로만 받는다.
  - 기존 자연어 route 자동 선택 기능은 제거한다.
  - 짧은 요청 저장용 public `submit`도 제거한다.
  - `--from-request` fallback은 추가하지 않는다.
- 이유:
  - 사용자가 route를 앞으로 전부 plan 문서대로만 쓰겠다고 명시했다.
  - 자동 혼합을 남기면 route가 다시 "작업 배치기"와 "계획문서 실행 변환기" 사이에서 의미가 흐려진다.

## Plan Quality Check

- Alternative considered: 별도 `adopt-plan` 명령을 추가하는 방식을 검토했다. 그러나 사용자가 route 자체를 앞으로 plan-first 문서 기준으로 쓰겠다고 했으므로 route의 정체성을 바꾸는 쪽이 더 맞다.
- Alternative considered: `--from-request`로 기존 자연어 route를 fallback 유지하는 방식을 검토했다. 그러나 fallback이 남으면 route의 기본 의미가 다시 느슨해지고, 스킬 호출 `$코덱스플로우 <요청>`의 해석도 계속 혼란스러워진다.
- Why this plan: 실행 단계는 이미 `plan.md`를 source of truth로 읽는 구조가 있으므로, 앞단 route만 plan-first source를 채택하도록 바꾸면 전체 흐름이 더 일관된다.
- What this plan may still miss: plan-first 문서 형식이 다양할 수 있어 ticket extraction 정확도가 낮을 수 있다. 초기 버전은 낮은 confidence일 때 단일 ticket과 review warning을 남겨야 한다.
- When to stop and revise: source plan에서 ticket을 안정적으로 추출하지 못하거나, 기존 run-next prompt가 source plan context를 잃는다면 queue 생성 방식을 멈추고 다시 설계한다.

## 검토용 결과물

- 계획 MD: 이 문서
- 테스트 링크:
  - Localhost: 해당 없음. CLI/문서/상태 모델 계획이며 브라우저 UI가 없다.
  - Deploy: 해당 없음. 배포 작업이 아니다.
- 상태: planned
- 실제 동작:
  - 현재 없음. 이 문서는 구현 전 계획이다.
- Mock:
  - 없음.

## HTML 생략 보고서

- 판정: 생략 가능
- 생략 사유:
  - 이번 작업은 CLI route contract, state files, source plan snapshot, ticket extraction, queue generation에 대한 백엔드/운영 계획이다.
  - 사용자가 눈으로 검토해야 하는 화면, 인터랙션, 디자인 변경이 없다.
- 대체 검토물:
  - 이 계획 문서
  - 예정 검증 명령: `python3 -m pytest tests/test_route_plan_first_source.py tests/test_crack_parity.py tests/test_runner_brief.py`
- 테스트 링크:
  - Localhost: 해당 없음. CLI 테스트로 검증한다.
  - Deploy: 해당 없음.
- 사용자가 바로 열어볼 링크:
  - `docs/plans/2026-06-02-route-plan-first-source-of-truth-plan.md`

## Success Criteria

- `route docs/plans/example.md --auto-resolve`가 source plan snapshot을 만들고 source metadata를 남긴다.
- `route "짧은 요청"`은 실패 안내를 출력하고 ticket/plan을 만들지 않는다.
- `submit "짧은 요청"`은 더 이상 public command로 동작하지 않는다.
- `$코덱스플로우 플랜대로 실행`은 현재/직전 대화 맥락에서 단일 plan-first 문서를 찾을 수 있을 때만 그 문서로 route한다.
- plan-first 문서를 찾을 수 없거나 후보가 여러 개면 "plan-first 문서를 말해주거나 Markdown 경로를 넘겨 달라"고 묻고 멈춘다.
- source plan에서 ticket 후보를 만들고 macro-plan을 작성한다.
- queue units가 ticket과 source section을 참조한다.
- run-next prompt가 full source plan snapshot, macro plan, selected ticket, selected commit unit을 모두 포함한다.
- source plan drift를 dashboard/review/run 단계에서 감지한다.
- 기존 자연어 route 처리 방식은 제거된다. `route`는 plan-first Markdown 문서 경로만 받는다.

## Verification Commands

```bash
python3 -m pytest tests/test_route_plan_first_source.py
python3 -m pytest tests/test_crack_parity.py tests/test_runner_brief.py
python3 scripts/codex_flow.py --repo /tmp/<repo> route docs/plans/example.md --auto-resolve
python3 scripts/codex_flow.py --repo /tmp/<repo> route "짧은 요청"
python3 scripts/codex_flow.py --repo /tmp/<repo> dashboard
```

Expected CLI behavior:

```text
route docs/plans/example.md -> source_plan_adopted + plan_created + queue_created
route "짧은 요청" -> non-zero, guidance message, no ticket_created
submit "짧은 요청" -> command unavailable
$코덱스플로우 플랜대로 실행 + one recent plan-first doc -> route <that-doc>
$코덱스플로우 플랜대로 실행 + no/ambiguous plan-first doc -> ask for plan-first document/path
```

## Open Risks

- plan-first 문서가 항상 일정한 heading 구조를 가진다는 보장이 없다.
- Codex planner가 source plan을 다시 써버리면 원본 보존 원칙이 깨질 수 있다.
- 기존 route tests는 자연어 요청 route를 전제로 하므로 제거 또는 재작성해야 한다.
- plan-local tickets와 top-level `.codex-flow/tickets`의 관계를 너무 복잡하게 만들면 dashboard가 읽기 어려워질 수 있다.
- `plan_readiness.sync_queue_cache_from_plan(...)`가 queue unit을 재생성하면서 `ticket_id`나 `source_plan_ref`를 잃을 수 있으므로 보존 테스트가 필요하다.
- `router_agent.py`가 더 이상 route에서 쓰이지 않으면 제거 범위를 신중히 잡아야 한다. 다른 명령이 쓰지 않는지 `rg`로 확인한 뒤 처리한다.

## Suggested Next Step

기존 자연어 route 제거를 기준으로 구현 계획을 실행한다.

추천 결정:

```text
A: route는 plan-first 문서 경로만 받고, 기존 자연어 route 기능은 제거한다.
```
