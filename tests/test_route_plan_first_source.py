from __future__ import annotations

import json

import pytest

from codex_flow import briefs, cli, inbox, plan_readiness, pr, runner
from codex_flow.dashboard import render_dashboard


def write_source_plan(tmp_path, content: str | None = None):
    plan_path = tmp_path / "docs" / "plans" / "example-plan.md"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        content
        or "\n".join(
            [
                "# Example Plan",
                "",
                "## Implementation Plan",
                "",
                "### Commit 1: Prepare source route",
                "",
                "- Add source route handling.",
                "",
                "### Commit 2: Verify source route",
                "",
                "- Add route verification.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return plan_path


def test_submit_is_not_public_cli_command():
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["submit", "short request"])

    assert exc.value.code == 2


def test_route_rejects_non_markdown_source(tmp_path, capsys):
    status = cli.main(["--repo", str(tmp_path), "route", "short request", "--auto-resolve"])

    output = capsys.readouterr().out
    assert status == 1
    assert "route requires a plan-first Markdown file path" in output
    assert not list((tmp_path / ".codex-flow" / "tickets").glob("*.md"))
    assert not list((tmp_path / ".codex-flow" / "plans").glob("*"))


def test_route_adopts_plan_first_markdown_source(tmp_path, capsys):
    source = write_source_plan(tmp_path)

    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])

    output = capsys.readouterr().out
    assert status == 0
    assert "source_plan_adopted:" in output
    assert "plan_created:" in output
    plan_dirs = list((tmp_path / ".codex-flow" / "plans").glob("*"))
    assert len(plan_dirs) == 1
    plan_dir = plan_dirs[0]
    assert (plan_dir / "source-plan.md").read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    metadata = json.loads((plan_dir / "source.json").read_text(encoding="utf-8"))
    assert metadata["source_path"] == "docs/plans/example-plan.md"
    assert metadata["source_repo"] == str(tmp_path.resolve())
    assert metadata["source_plan_path"] == str(source.resolve())
    assert metadata["source_plan_sha256"] == metadata["source_sha256"]
    assert metadata["route_mode"] == "plan_first_source"
    assert metadata["extraction_confidence"] == "high"
    queue = json.loads((plan_dir / "queue.json").read_text(encoding="utf-8"))
    assert queue["execution_repo"] == str(tmp_path.resolve())
    assert queue["worktree_path"] == str(tmp_path.resolve())
    assert queue["source_repo"] == str(tmp_path.resolve())
    assert queue["source_plan_path"] == str(source.resolve())
    assert queue["source_plan_sha256"] == metadata["source_sha256"]
    assert [unit["ticket_id"] for unit in queue["units"]] == ["ticket-001", "ticket-002"]
    assert queue["units"][0]["source_plan_ref"]["section"] == "### Commit 1: Prepare source route"
    assert (plan_dir / "macro-plan.md").exists()
    assert (plan_dir / "tickets" / "ticket-001.md").exists()


def test_route_holds_low_confidence_source_at_human_gate(tmp_path, capsys):
    source = write_source_plan(
        tmp_path,
        "\n".join(
            [
                "# Broad Plan",
                "",
                "## Goal",
                "",
                "- This plan has no commit headings yet.",
                "",
            ]
        ),
    )

    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])

    assert status == 0
    capsys.readouterr()
    plan_dir = next((tmp_path / ".codex-flow" / "plans").glob("*"))
    queue = json.loads((plan_dir / "queue.json").read_text(encoding="utf-8"))
    metadata = json.loads((plan_dir / "source.json").read_text(encoding="utf-8"))
    assert metadata["extraction_confidence"] == "low"
    assert len(queue["units"]) == 1
    assert queue["units"][0]["ticket_id"] == "ticket-001"
    assert queue["units"][0]["extraction_confidence"] == "low"
    assert queue["units"][0]["status"] == "human_gate"
    assert "Execute source plan" in (plan_dir / "plan.md").read_text(encoding="utf-8")


def test_run_all_stops_on_low_confidence_human_gate(tmp_path, capsys):
    source = write_source_plan(
        tmp_path,
        "\n".join(
            [
                "# Broad Plan",
                "",
                "## Goal",
                "",
                "- This plan has no commit headings yet.",
                "",
            ]
        ),
    )
    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])
    assert status == 0
    capsys.readouterr()
    plan_dir = next((tmp_path / ".codex-flow" / "plans").glob("*"))

    status = cli.main(["--repo", str(tmp_path), "run-all", "--plan", str(plan_dir / "plan.md")])

    output = capsys.readouterr().out
    assert status == 1
    assert "run_all: human_gate" in output
    assert "Unit is held at human_gate" in output


def test_source_queue_fields_survive_queue_cache_sync(tmp_path, capsys):
    source = write_source_plan(tmp_path)

    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])

    assert status == 0
    capsys.readouterr()
    plan_dir = next((tmp_path / ".codex-flow" / "plans").glob("*"))
    synced = plan_readiness.sync_queue_cache_from_plan(plan_dir / "plan.md")
    assert synced["units"][0]["ticket_id"] == "ticket-001"
    assert synced["units"][0]["source_plan_ref"]["section"] == "### Commit 1: Prepare source route"
    assert synced["units"][0]["source_plan_ref"]["path"] == "docs/plans/example-plan.md"


def test_source_route_does_not_turn_later_commits_into_final_gate_units(tmp_path, capsys):
    source = write_source_plan(
        tmp_path,
        "\n".join(
            [
                "# Four Commit Plan",
                "",
                "### Commit 1: Prepare",
                "",
                "- Prepare.",
                "",
                "### Commit 2: Build",
                "",
                "- Build.",
                "",
                "### Commit 3: Integrate",
                "",
                "- Integrate.",
                "",
                "### Commit 4: Polish",
                "",
                "- Polish.",
                "",
            ]
        ),
    )

    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])

    assert status == 0
    capsys.readouterr()
    plan_dir = next((tmp_path / ".codex-flow" / "plans").glob("*"))
    queue = json.loads((plan_dir / "queue.json").read_text(encoding="utf-8"))
    assert len(queue["units"]) == 4
    assert queue["units"][3]["title"] == "Polish"
    assert "src/**" in queue["units"][3]["allowed_paths"]
    assert "review-all-in-one" not in queue["units"][3]["required_skills"]


def test_source_route_derives_allowed_paths_from_commit_target_files(tmp_path, capsys):
    source = write_source_plan(
        tmp_path,
        "\n".join(
            [
                "# Target Path Plan",
                "",
                "### Commit 1: Update contracts",
                "",
                "- 대상 파일:",
                "  - `/Users/moonsoo/projects/example/ignored.md`",
                f"  - `{tmp_path / 'flow-architecture-map' / 'SKILL.md'}`",
                "  - `flow-architecture-map/references/views/user-flow.md`",
                "",
                "### Commit 2: Update templates",
                "",
                "- 대상 파일:",
                "  - `flow-architecture-map/assets/templates/flow-architecture-map-folder/user-flow.html`",
                "  - `flow-architecture-map/assets/templates/flow-architecture-map-folder/assets/styles.css`",
                "",
            ]
        ),
    )

    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])

    assert status == 0
    capsys.readouterr()
    plan_dir = next((tmp_path / ".codex-flow" / "plans").glob("*"))
    queue = json.loads((plan_dir / "queue.json").read_text(encoding="utf-8"))
    plan_text = (plan_dir / "plan.md").read_text(encoding="utf-8")
    assert queue["units"][0]["allowed_paths"] == [
        "flow-architecture-map/SKILL.md",
        "flow-architecture-map/references/views/user-flow.md",
    ]
    assert queue["units"][0]["external_allowed_paths"] == ["/Users/moonsoo/projects/example/ignored.md"]
    assert queue["units"][0]["status"] == "human_gate"
    assert queue["units"][1]["allowed_paths"] == [
        "flow-architecture-map/assets/templates/flow-architecture-map-folder/user-flow.html",
        "flow-architecture-map/assets/templates/flow-architecture-map-folder/assets/styles.css",
    ]
    assert "flow-architecture-map/SKILL.md" in plan_text
    assert "/Users/moonsoo/projects/example/ignored.md" in plan_text
    assert "frontend/**" not in queue["units"][1]["allowed_paths"]


def test_source_route_preserves_source_skill_routing_manifest(tmp_path, capsys):
    source = write_source_plan(
        tmp_path,
        "\n".join(
            [
                "# Routed Skill Plan",
                "",
                "## Skill Routing Manifest",
                "",
                "| Phase | Required skills | Optional skills | Evidence |",
                "| --- | --- | --- | --- |",
                "| Commit 1: Update contracts | `flow-architecture-map`, `structure-map-html` | `review-all-in-one` | Source-specific routing. |",
                "| Commit 2: Update templates | `plan-first-implementation` | `qa-gate` | Template routing. |",
                "| Final Gate | `review-all-in-one`, `qa-gate` | `테스트` | Final check. |",
                "",
                "## Implementation Plan",
                "",
                "### Commit 1: Update contracts",
                "",
                "- 대상 파일:",
                "  - `flow-architecture-map/SKILL.md`",
                "",
                "### Commit 2: Update templates",
                "",
                "- 대상 파일:",
                "  - `flow-architecture-map/assets/template.html`",
                "",
            ]
        ),
    )

    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])

    assert status == 0
    capsys.readouterr()
    plan_dir = next((tmp_path / ".codex-flow" / "plans").glob("*"))
    queue = json.loads((plan_dir / "queue.json").read_text(encoding="utf-8"))
    plan_text = (plan_dir / "plan.md").read_text(encoding="utf-8")
    synced = plan_readiness.sync_queue_cache_from_plan(plan_dir / "plan.md")
    assert queue["units"][0]["required_skills"] == [
        "plan-first-implementation",
        "flow-architecture-map",
        "structure-map-html",
    ]
    assert queue["units"][0]["optional_skills"] == ["review-all-in-one"]
    assert queue["units"][0]["skill_routing_evidence"] == "Source-specific routing."
    assert synced["units"][0]["required_skills"] == [
        "plan-first-implementation",
        "flow-architecture-map",
        "structure-map-html",
    ]
    assert (
        "| Commit 1: Update contracts | `plan-first-implementation`, `flow-architecture-map`, `structure-map-html` "
        "| `review-all-in-one` | Source-specific routing. |"
    ) in plan_text


def test_route_queues_valid_source_when_pr_lock_is_active(tmp_path, capsys):
    source = write_source_plan(tmp_path)
    pr.write_pr_lock(tmp_path, "codex/open", "https://github.com/example/repo/pull/1", "reviewing")

    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])

    output = capsys.readouterr().out
    assert status == 0
    assert "queued source plan due to active PR lock" in output
    assert not list((tmp_path / ".codex-flow" / "plans").glob("*"))
    requests = inbox.read_inbox_requests(tmp_path / ".codex-flow" / "inbox.md")
    assert len(requests) == 1
    assert requests[0].prompt == str(source.resolve())
    assert "source plan route deferred" in requests[0].reason


def test_drain_routes_deferred_source_plan_after_pr_lock_clears(tmp_path, capsys):
    source = write_source_plan(tmp_path)
    pr.write_pr_lock(tmp_path, "codex/open", "https://github.com/example/repo/pull/1", "reviewing")
    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])
    assert status == 0
    capsys.readouterr()
    pr.clear_pr_lock(tmp_path)

    status = cli.main(["--repo", str(tmp_path), "drain"])

    output = capsys.readouterr().out
    assert status == 0
    assert "drain: routed 1 request(s)" in output
    assert not inbox.read_inbox_requests(tmp_path / ".codex-flow" / "inbox.md")
    assert list((tmp_path / ".codex-flow" / "plans").glob("*"))


def test_drain_keeps_non_source_request_in_inbox(tmp_path, capsys):
    inbox.append_inbox_request(tmp_path, "short request", "Legacy raw request.")

    status = cli.main(["--repo", str(tmp_path), "drain"])

    output = capsys.readouterr().out
    assert status == 0
    assert "drain: source plan required" in output
    assert len(inbox.read_inbox_requests(tmp_path / ".codex-flow" / "inbox.md")) == 1
    assert not list((tmp_path / ".codex-flow" / "plans").glob("*"))


def test_run_next_prompt_includes_source_context(tmp_path, capsys):
    source = write_source_plan(tmp_path)
    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])
    assert status == 0
    capsys.readouterr()
    plan_dir = next((tmp_path / ".codex-flow" / "plans").glob("*"))

    result = runner.run_next(plan_dir / "plan.md", dry_run=True)

    prompt = result["prompt"]
    assert "## Source Plan Snapshot" in prompt
    assert "This is the original plan-first document adopted by route." in prompt
    assert "### Commit 1: Prepare source route" in prompt
    assert "## Macro Plan Context" in prompt
    assert "## Selected Ticket" in prompt
    assert "ticket-001: Prepare source route" in prompt


def test_run_next_stops_on_source_drift_without_override(tmp_path, capsys):
    source = write_source_plan(tmp_path)
    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])
    assert status == 0
    capsys.readouterr()
    plan_dir = next((tmp_path / ".codex-flow" / "plans").glob("*"))
    source.write_text(source.read_text(encoding="utf-8") + "\n\n## Later Change\n", encoding="utf-8")

    status = cli.main(["--repo", str(tmp_path), "run-next", "--plan", str(plan_dir / "plan.md"), "--dry-run"])

    output = capsys.readouterr().out
    assert status == 1
    assert "action: source_drift" in output
    assert "source changed" in output


def test_run_next_accepts_source_drift_override(tmp_path, capsys):
    source = write_source_plan(tmp_path)
    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])
    assert status == 0
    capsys.readouterr()
    plan_dir = next((tmp_path / ".codex-flow" / "plans").glob("*"))
    source.write_text(source.read_text(encoding="utf-8") + "\n\n## Later Change\n", encoding="utf-8")

    status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "run-next",
            "--plan",
            str(plan_dir / "plan.md"),
            "--dry-run",
            "--accept-source-drift",
        ]
    )

    output = capsys.readouterr().out
    assert status == 0
    assert "dry_run: prompt not written and queue not changed" in output


def test_run_all_stops_on_source_drift_without_override(tmp_path, capsys):
    source = write_source_plan(tmp_path)
    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])
    assert status == 0
    capsys.readouterr()
    plan_dir = next((tmp_path / ".codex-flow" / "plans").glob("*"))
    source.write_text(source.read_text(encoding="utf-8") + "\n\n## Later Change\n", encoding="utf-8")

    status = cli.main(["--repo", str(tmp_path), "run-all", "--plan", str(plan_dir / "plan.md"), "--dry-run"])

    output = capsys.readouterr().out
    assert status == 1
    assert "run_all: source_drift" in output


def test_open_pr_auto_resolve_stops_on_source_drift(tmp_path, capsys):
    source = write_source_plan(tmp_path)
    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])
    assert status == 0
    capsys.readouterr()
    plan_dir = next((tmp_path / ".codex-flow" / "plans").glob("*"))
    source.write_text(source.read_text(encoding="utf-8") + "\n\n## Later Change\n", encoding="utf-8")

    status = cli.main(
        [
            "--repo",
            str(tmp_path),
            "open-pr",
            "--plan",
            str(plan_dir / "plan.md"),
            "--auto-resolve",
            "--no-execute-units",
        ]
    )

    output = capsys.readouterr().out
    assert status == 1
    assert "auto_resolve_units:" in output
    assert "source_drift=1" in output
    assert "pr_dry_run:" not in output


def test_dashboard_and_review_warn_about_source_drift(tmp_path, capsys):
    source = write_source_plan(tmp_path)
    status = cli.main(["--repo", str(tmp_path), "route", str(source), "--auto-resolve"])
    assert status == 0
    capsys.readouterr()
    plan_dir = next((tmp_path / ".codex-flow" / "plans").glob("*"))
    source.write_text(source.read_text(encoding="utf-8") + "\n\n## Later Change\n", encoding="utf-8")

    dashboard = render_dashboard(tmp_path)
    review_path = briefs.write_review(plan_dir / "plan.md")
    review = review_path.read_text(encoding="utf-8")

    assert "Source drift: source changed" in dashboard
    assert "## Source Drift" in review
    assert "source changed" in review
