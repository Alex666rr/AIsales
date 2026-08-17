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
    closed: bool = False


class FakeTrelloTransport:
    def __init__(self) -> None:
        self.lists: list[dict[str, Any]] = []
        self.cards: list[FakeCard] = []
        self.calls: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> Any:
        self.calls.append((method, path))
        data = data or {}
        if method == "GET" and path == "/1/boards/board-short":
            return {"id": "board-full", "closed": False}
        if method == "GET" and path.endswith("/lists"):
            return list(self.lists)
        if method == "GET" and path.endswith("/cards"):
            return [
                {
                    "id": card.id,
                    "name": card.name,
                    "desc": card.description,
                    "idList": card.id_list,
                    "closed": card.closed,
                }
                for card in self.cards
            ]
        if method == "POST" and path == "/1/lists":
            assert data["idBoard"] == "board-full"
            item = {"id": f"list-{len(self.lists) + 1}", "name": data["name"], "closed": False}
            self.lists.append(item)
            return item
        if method == "POST" and path == "/1/cards":
            card = FakeCard(
                id=f"card-{len(self.cards) + 1}",
                name=data["name"],
                description=data["desc"],
                id_list=data["idList"],
            )
            self.cards.append(card)
            return {"id": card.id}
        if method == "PUT" and path.startswith("/1/cards/"):
            card = next(card for card in self.cards if path.endswith(card.id))
            card.description = data["desc"]
            return {"id": card.id}
        raise AssertionError(f"Unexpected request: {method} {path}")

    def card(self, key: str) -> FakeCard:
        return next(card for card in self.cards if f"aisales-stage:{key}" in card.description)


def build_synchronizer(fake: FakeTrelloTransport) -> TrelloBoardSynchronizer:
    return TrelloBoardSynchronizer(
        board_id="board-short",
        transport=fake,
    )


def test_sync_creates_missing_lists_and_cards_once() -> None:
    fake = FakeTrelloTransport()
    synchronizer = build_synchronizer(fake)

    first = synchronizer.sync()
    second = synchronizer.sync()

    assert first.created_lists == 4
    assert first.created_cards == 4
    assert second.created_lists == 0
    assert second.created_cards == 0
    assert second.updated_cards == 0
    assert len(fake.lists) == 4
    assert len(fake.cards) == 4


def test_sync_preserves_human_card_text_and_list_position() -> None:
    fake = FakeTrelloTransport()
    synchronizer = build_synchronizer(fake)
    synchronizer.sync()
    card = fake.card("stage-1")
    done_list = next(item for item in fake.lists if item["name"] == "Готово")
    card.id_list = done_list["id"]
    card.description = f"Проверить вручную\n{card.description}"

    synchronizer.sync()

    assert card.id_list == done_list["id"]
    assert card.description.startswith("Проверить вручную\n")


def test_missing_variable_is_actionable_without_echoing_values() -> None:
    with pytest.raises(ConfigurationError, match="TRELLO_TOKEN") as error:
        TrelloBoardSynchronizer.from_environment({"TRELLO_API_KEY": "secret-value"})

    assert "secret-value" not in str(error.value)
