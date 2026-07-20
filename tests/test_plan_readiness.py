from __future__ import annotations

import json

from codex_flow import plan_readiness


def test_parse_commit_units_and_completed_numbers():
    plan = "\n".join(
        [
            "# Plan",
            "Branch: codex/demo",
            "Title: Demo",
            "",
            "### Commit 1: First",
            "",
            "- Do first",
            "",
            "### Commit 2: Second",
            "",
            "- Do second",
        ]
    )
    units = plan_readiness.parse_commit_units(plan)

    assert [unit.number for unit in units] == [1, 2]
    assert units[0].title == "First"
    assert plan_readiness.branch_name_from_plan(plan) == "codex/demo"
    assert plan_readiness.title_from_plan(plan) == "Demo"
    assert plan_readiness.completed_commit_unit_numbers("- Completed commit unit 1.\n- done unit-002\n") == {1, 2}


def test_check_plan_ready_selects_next_unit():
    plan = "### Commit 1: First\n\nA\n\n### Commit 2: Second\n\nB\n"
    readiness = plan_readiness.check_plan_ready(plan, "- Completed commit unit 1.\n")

    assert not readiness.ready
    assert readiness.next_unit
    assert readiness.next_unit.number == 2


def test_sync_queue_cache_from_plan_without_existing_json(tmp_path):
    plan_dir = tmp_path / ".codex-flow" / "plans" / "demo"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.md").write_text("Branch: codex/demo\nTitle: Demo\n\n### Commit 1: First\n\nA\n", encoding="utf-8")
    (plan_dir / "log.md").write_text("- Completed commit unit 1.\n", encoding="utf-8")

    queue = plan_readiness.sync_queue_cache_from_plan(plan_dir / "plan.md")

    assert queue["branch"] == "codex/demo"
    assert queue["units"][0]["status"] == "done"
    assert json.loads((plan_dir / "queue.json").read_text(encoding="utf-8"))["units"][0]["id"] == "unit-001"


def test_parse_skill_routing_manifest_and_syncs_to_queue(tmp_path):
    plan_dir = tmp_path / ".codex-flow" / "plans" / "skills"
    plan_dir.mkdir(parents=True)
    plan_text = "\n".join(
        [
            "Branch: codex/skills",
            "Title: Skills",
            "",
            "## Skill Routing Manifest",
            "",
            "| Phase | Required skills | Optional skills | Evidence |",
            "| --- | --- | --- | --- |",
            "| Commit 1: First | `요청개선`, `mission-completion-harness` | `community-research` | 요청 정리 |",
            "| Final Gate | `review-all-in-one`, `qa-gate` | `checkpoint` | 최종 검토 |",
            "",
            "## Commit Units",
            "",
            "### Commit 1: First",
            "",
            "A",
        ]
    )
    (plan_dir / "plan.md").write_text(plan_text, encoding="utf-8")
    (plan_dir / "log.md").write_text("", encoding="utf-8")

    entries = plan_readiness.parse_skill_routing_manifest(plan_text)
    commit_entry = plan_readiness.skill_routing_for_commit(plan_text, 1)
    final_entry = plan_readiness.final_gate_skill_routing(plan_text)
    queue = plan_readiness.sync_queue_cache_from_plan(plan_dir / "plan.md")

    assert [entry.phase for entry in entries] == ["Commit 1: First", "Final Gate"]
    assert commit_entry
    assert commit_entry.required_skills == ("요청개선", "mission-completion-harness")
    assert final_entry
    assert final_entry.required_skills == ("review-all-in-one", "qa-gate")
    assert queue["units"][0]["required_skills"] == ["요청개선", "mission-completion-harness"]
