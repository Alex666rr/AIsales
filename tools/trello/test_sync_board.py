from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from tools.trello.sync_board import ConfigurationError, TrelloBoardSynchronizer


@dataclass
class FakeCard:
    id: str
    name: str
    description: str
    id_list: str
    due_complete: bool = False
    closed: bool = False


class FakeTrelloTransport:
    def __init__(self, list_names: tuple[str, ...] = ()) -> None:
        self.lists = [
            {"id": f"legacy-{index}", "name": name, "closed": False}
            for index, name in enumerate(list_names, start=1)
        ]
        self.cards: list[FakeCard] = []

    def request(self, method: str, path: str, *, params: dict[str, str] | None = None, data: dict[str, str] | None = None) -> Any:
        data = data or {}
        if method == "GET" and path == "/1/boards/board-short":
            return {"id": "board-full", "closed": False}
        if method == "GET" and path.endswith("/lists"):
            return list(self.lists)
        if method == "GET" and path.endswith("/cards"):
            return [{"id": card.id, "name": card.name, "desc": card.description, "idList": card.id_list, "dueComplete": card.due_complete, "closed": card.closed} for card in self.cards]
        if method == "POST" and path == "/1/lists":
            assert data["idBoard"] == "board-full"
            item = {"id": f"list-{len(self.lists) + 1}", "name": data["name"], "closed": False}
            self.lists.append(item)
            return item
        if method == "PUT" and path.startswith("/1/lists/"):
            list_id = path.split("/")[3]
            item = next(item for item in self.lists if item["id"] == list_id)
            item["closed"] = data["value"] == "true"
            return item
        if method == "POST" and path == "/1/cards":
            card = FakeCard(f"card-{len(self.cards) + 1}", data["name"], data["desc"], data["idList"], data.get("dueComplete") == "true")
            self.cards.append(card)
            return {"id": card.id}
        if method == "PUT" and path.startswith("/1/cards/"):
            card = next(card for card in self.cards if path.endswith(card.id))
            if "desc" in data:
                card.description = data["desc"]
            if "dueComplete" in data:
                card.due_complete = data["dueComplete"] == "true"
            return {"id": card.id}
        raise AssertionError(f"Unexpected request: {method} {path}")

    def card(self, code: str) -> FakeCard:
        return next(card for card in self.cards if card.name.startswith(f"{code} ·"))


def build_synchronizer(fake: FakeTrelloTransport) -> TrelloBoardSynchronizer:
    return TrelloBoardSynchronizer(board_id="board-short", transport=fake)


def test_sync_builds_readable_stage_roadmap_without_hidden_markup() -> None:
    fake = FakeTrelloTransport()
    result = build_synchronizer(fake).sync()

    assert result.created_lists == 6
    assert result.created_cards == 36
    assert {item["name"] for item in fake.lists if not item["closed"]} == {
        "📍 Сейчас", "✅ Stage 0 — Telegram prototype", "🚧 Stage 1 — Sales infrastructure",
        "🗂 Stage 2 — AI manager", "🗂 Stage 3 — Quality and optimization", "📦 Архив",
    }
    next_card = fake.card("S1.03")
    assert "Веб-приложение и администрирование" in next_card.name
    assert "Статус: Следующее" in next_card.description
    assert "docs/superpowers/plans/2026-08-07-ai-sales-manager-stage-1-sales-infrastructure.md" in next_card.description
    assert "<!--" not in next_card.description


def test_sync_preserves_human_notes_and_is_idempotent() -> None:
    fake = FakeTrelloTransport()
    synchronizer = build_synchronizer(fake)
    synchronizer.sync()
    card = fake.card("S1.03")
    card.description += "Проверить макеты вместе с владельцем."

    updated = synchronizer.sync()
    repeated = synchronizer.sync()

    assert updated.updated_cards == 0
    assert "Проверить макеты вместе с владельцем." in card.description
    assert repeated.created_lists == repeated.created_cards == repeated.updated_cards == repeated.archived_lists == 0


def test_sync_marks_done_roadmap_cards_complete_and_keeps_open_work_unchecked() -> None:
    fake = FakeTrelloTransport()
    synchronizer = build_synchronizer(fake)

    synchronizer.sync()

    assert fake.card("S0.01").due_complete is True
    assert fake.card("S1.02").due_complete is True
    assert fake.card("S1.03").due_complete is False

    fake.card("S0.01").due_complete = False
    result = synchronizer.sync()

    assert result.updated_cards == 1
    assert fake.card("S0.01").due_complete is True


def test_sync_archives_only_known_previous_generated_lists() -> None:
    fake = FakeTrelloTransport(("Нужно сделать", "В процессе", "Готово", "Бэклог", "В работе", "На проверке", "Мои заметки"))

    result = build_synchronizer(fake).sync()

    archived = {item["name"] for item in fake.lists if item["closed"]}
    assert result.archived_lists == 6
    assert archived == {"Нужно сделать", "В процессе", "Готово", "Бэклог", "В работе", "На проверке"}
    assert next(item for item in fake.lists if item["name"] == "Мои заметки")["closed"] is False


def test_missing_variable_is_actionable_without_echoing_values() -> None:
    with pytest.raises(ConfigurationError, match="TRELLO_TOKEN") as error:
        TrelloBoardSynchronizer.from_environment({"TRELLO_API_KEY": "secret-value"})

    assert "secret-value" not in str(error.value)
