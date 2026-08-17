"""Idempotently synchronize AIsales delivery milestones to a Trello board.

Run locally only. Credentials are read from environment variables and are never
written to the board, output, or repository.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ConfigurationError(ValueError):
    """Raised when the local operator has not configured Trello access."""


class TrelloRequestError(RuntimeError):
    """Raised when Trello cannot complete a requested board operation."""


class TrelloTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class CardSpec:
    key: str
    name: str
    list_name: str
    status: str


@dataclass(frozen=True)
class SyncResult:
    created_lists: int
    created_cards: int
    updated_cards: int


LIST_NAMES = ("Бэклог", "В работе", "На проверке", "Готово")
CARD_SPECS = (
    CardSpec("stage-0", "Этап 0 — Telegram foundation", "Готово", "Базовый Telegram-контур готов"),
    CardSpec("stage-1", "Этап 1 — Sales infrastructure", "В работе", "Доступ и рабочая инфраструктура"),
    CardSpec("stage-2", "Этап 2 — AI manager", "Бэклог", "Ожидает завершения Stage 1"),
    CardSpec("stage-3", "Этап 3 — Quality and optimization", "Бэклог", "Ожидает завершения Stage 2"),
)


def _managed_block(spec: CardSpec) -> str:
    return (
        "\n\n---\n"
        f"<!-- aisales-stage:{spec.key} -->\n"
        f"Статус проекта: {spec.status}\n"
        "<!-- /aisales-stage -->"
    )


def _replace_managed_block(description: str, spec: CardSpec) -> str:
    marker_start = f"<!-- aisales-stage:{spec.key} -->"
    marker_end = "<!-- /aisales-stage -->"
    start = description.find(marker_start)
    if start == -1:
        return f"{description.rstrip()}{_managed_block(spec)}"
    separator_start = description.rfind("---", 0, start)
    if separator_start != -1 and not description[separator_start + 3 : start].strip():
        start = separator_start
    end = description.find(marker_end, start)
    if end == -1:
        return f"{description.rstrip()}{_managed_block(spec)}"
    end += len(marker_end)
    prefix = description[:start].rstrip()
    block = _managed_block(spec).lstrip() if not prefix else _managed_block(spec)
    return f"{prefix}{block}{description[end:].lstrip()}"


class HttpTrelloTransport:
    def __init__(self, api_key: str, token: str) -> None:
        self._api_key = api_key
        self._token = token

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> Any:
        query = {"key": self._api_key, "token": self._token, **(params or {})}
        url = f"https://api.trello.com{path}?{urlencode(query)}"
        body = urlencode(data).encode("utf-8") if data else None
        request = Request(url, data=body, method=method)
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise TrelloRequestError(f"Trello request failed with HTTP {error.code}") from error
        except URLError as error:
            raise TrelloRequestError("Trello request could not reach the API") from error


class TrelloBoardSynchronizer:
    def __init__(self, board_id: str, transport: TrelloTransport) -> None:
        self._board_id = board_id
        self._transport = transport

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "TrelloBoardSynchronizer":
        values = os.environ if environ is None else environ
        required = ("TRELLO_API_KEY", "TRELLO_TOKEN", "TRELLO_BOARD_ID")
        missing = [name for name in required if not values.get(name, "").strip()]
        if missing:
            raise ConfigurationError(f"Missing required Trello variable(s): {', '.join(missing)}")
        return cls(
            board_id=values["TRELLO_BOARD_ID"].strip(),
            transport=HttpTrelloTransport(
                api_key=values["TRELLO_API_KEY"].strip(),
                token=values["TRELLO_TOKEN"].strip(),
            ),
        )

    def sync(self) -> SyncResult:
        board_id = self._resolved_board_id()
        lists = self._active_lists(board_id)
        created_lists = 0
        for list_name in LIST_NAMES:
            if list_name not in lists:
                created = self._transport.request(
                    "POST",
                    "/1/lists",
                    data={"idBoard": board_id, "name": list_name, "pos": "bottom"},
                )
                lists[list_name] = created
                created_lists += 1

        cards = self._managed_cards(board_id)
        created_cards = 0
        updated_cards = 0
        for spec in CARD_SPECS:
            existing = cards.get(spec.key)
            if existing is None:
                self._transport.request(
                    "POST",
                    "/1/cards",
                    data={
                        "idList": str(lists[spec.list_name]["id"]),
                        "name": spec.name,
                        "desc": _managed_block(spec).lstrip(),
                    },
                )
                created_cards += 1
                continue

            updated_description = _replace_managed_block(str(existing.get("desc", "")), spec)
            if updated_description != existing.get("desc", ""):
                self._transport.request(
                    "PUT",
                    f"/1/cards/{existing['id']}",
                    data={"desc": updated_description},
                )
                updated_cards += 1
        return SyncResult(created_lists, created_cards, updated_cards)

    def _resolved_board_id(self) -> str:
        board = self._transport.request("GET", f"/1/boards/{self._board_id}")
        if not isinstance(board, dict) or board.get("closed", False) or not board.get("id"):
            raise TrelloRequestError("Trello returned an unavailable board")
        return str(board["id"])

    def _active_lists(self, board_id: str) -> dict[str, dict[str, Any]]:
        lists = self._transport.request("GET", f"/1/boards/{board_id}/lists")
        if not isinstance(lists, list):
            raise TrelloRequestError("Trello returned an invalid list response")
        return {
            str(item["name"]): item
            for item in lists
            if isinstance(item, dict) and not item.get("closed", False) and item.get("id") and item.get("name")
        }

    def _managed_cards(self, board_id: str) -> dict[str, dict[str, Any]]:
        cards = self._transport.request("GET", f"/1/boards/{board_id}/cards")
        if not isinstance(cards, list):
            raise TrelloRequestError("Trello returned an invalid card response")
        found: dict[str, dict[str, Any]] = {}
        for item in cards:
            if not isinstance(item, dict) or item.get("closed", False):
                continue
            description = str(item.get("desc", ""))
            for spec in CARD_SPECS:
                if f"<!-- aisales-stage:{spec.key} -->" in description:
                    found[spec.key] = item
        return found


def main() -> int:
    try:
        result = TrelloBoardSynchronizer.from_environment().sync()
    except (ConfigurationError, TrelloRequestError) as error:
        print(f"Trello synchronization failed: {error}", file=sys.stderr)
        return 1
    print(
        "Trello board synchronized: "
        f"lists created={result.created_lists}, cards created={result.created_cards}, cards updated={result.updated_cards}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
