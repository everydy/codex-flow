from __future__ import annotations

from codex_flow import tickets


def test_submit_ticket_writes_ticket_and_inbox(tmp_path):
    ticket = tickets.submit_ticket("장비대여 캘린더 상태 개선", repo=tmp_path, project="equipment")

    assert ticket.path.exists()
    assert ticket.title == "장비대여 캘린더 상태 개선"
    assert ticket.project == "equipment"
    assert ticket.status == "inbox"

    inbox = tmp_path / ".codex-flow" / "inbox.md"
    inbox_text = inbox.read_text(encoding="utf-8")
    assert ticket.id in inbox_text
    assert "장비대여" in inbox_text


def test_first_inbox_ticket_returns_oldest(tmp_path):
    first = tickets.submit_ticket("첫 번째 작업", repo=tmp_path)
    tickets.submit_ticket("두 번째 작업", repo=tmp_path)

    assert tickets.first_inbox_ticket(tmp_path).id == first.id

