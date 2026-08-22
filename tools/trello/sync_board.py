"""Create a readable, local-only Trello roadmap for the AIsales project."""

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
    """Raised when the local Trello configuration is incomplete."""


class TrelloRequestError(RuntimeError):
    """Raised when Trello rejects or cannot receive a request."""


class TrelloTransport(Protocol):
    def request(self, method: str, path: str, *, params: dict[str, str] | None = None, data: dict[str, str] | None = None) -> Any: ...


@dataclass(frozen=True)
class RoadmapCard:
    code: str
    title: str
    list_name: str
    status: str
    goal: str
    plan_path: str
    next_action: str | None = None

    @property
    def is_complete(self) -> bool:
        """Return the Trello completion state derived from the roadmap status."""
        return self.status == "Готово"


@dataclass(frozen=True)
class SyncResult:
    created_lists: int
    created_cards: int
    updated_cards: int
    archived_lists: int


CURRENT_LIST = "📍 Сейчас"
STAGE_0_LIST = "✅ Stage 0 — Telegram prototype"
STAGE_1_LIST = "🚧 Stage 1 — Sales infrastructure"
STAGE_2_LIST = "🗂 Stage 2 — AI manager"
STAGE_3_LIST = "🗂 Stage 3 — Quality and optimization"
ARCHIVE_LIST = "📦 Архив"
LIST_NAMES = (CURRENT_LIST, STAGE_0_LIST, STAGE_1_LIST, STAGE_2_LIST, STAGE_3_LIST, ARCHIVE_LIST)
LEGACY_LIST_NAMES = frozenset({"Нужно сделать", "В процессе", "Готово", "Бэклог", "В работе", "На проверке"})
STAGE_0_PLAN = "docs/superpowers/plans/2026-08-07-ai-sales-manager-stage-0-telegram-prototype.md"
STAGE_1_PLAN = "docs/superpowers/plans/2026-08-07-ai-sales-manager-stage-1-sales-infrastructure.md"
STAGE_2_PLAN = "docs/superpowers/plans/2026-08-07-ai-sales-manager-stage-2-ai-manager.md"
STAGE_3_PLAN = "docs/superpowers/plans/2026-08-07-ai-sales-manager-stage-3-quality-optimization.md"
MASTER_PLAN = "docs/superpowers/plans/2026-08-07-ai-sales-manager-master-plan.md"


def _stage_cards(stage: str, list_name: str, plan_path: str, titles: tuple[str, ...], *, status: str = "Далее", start: int = 1) -> tuple[RoadmapCard, ...]:
    return tuple(
        RoadmapCard(
            code=f"{stage}.{index:02d}",
            title=title,
            list_name=list_name,
            status=status,
            goal=f"Выполнить блок «{title}» в рамках {stage}.",
            plan_path=plan_path,
        )
        for index, title in enumerate(titles, start=start)
    )


ROADMAP_CARDS = (
    RoadmapCard("INFO.01", "Как читать эту доску", CURRENT_LIST, "Справка", "Показывает общий маршрут AIsales и источник правды для каждого блока.", MASTER_PLAN),
    RoadmapCard("NOW.01", "Следующий блок — Telegram-аккаунты, боты и прокси", CURRENT_LIST, "Следующее", "Собрать операционную основу рабочих Telegram-аккаунтов, ботов и прокси в рамках Stage 1.", STAGE_1_PLAN, "Продолжить с единой моделью рабочих аккаунтов, ботами и назначением прокси."),
    *_stage_cards("S0", STAGE_0_LIST, STAGE_0_PLAN, (
        "Каркас Telegram-прототипа", "Контракты сессий и карантин", "Адаптеры авторизации", "Прокси и жизненный цикл подключений", "Сообщения и совместимость", "Контроль допуска Telegram/AI",
    ), status="Готово"),
    RoadmapCard("S1.01", "Основа API, базы данных, outbox и аудита", STAGE_1_LIST, "Готово", "Создать фундамент серверной части и аудита.", STAGE_1_PLAN),
    RoadmapCard("S1.02", "Организации, пользователи, роли и 2FA", STAGE_1_LIST, "Готово", "Настроить доступ, первого владельца, TOTP и recovery-коды.", STAGE_1_PLAN),
    RoadmapCard("S1.03", "Веб-приложение и администрирование", STAGE_1_LIST, "Готово", "Сделать web shell и безопасное управление staff.", STAGE_1_PLAN),
    *_stage_cards("S1", STAGE_1_LIST, STAGE_1_PLAN, (
        "Telegram-аккаунты, боты и прокси", "Контакты, идентичность и импорт", "Безопасные массовые операции", "Независимые статусы контактов", "Кампании, аудитории, цепочки и варианты", "Планировщик, лимиты и идемпотентная отправка", "Inbox, менеджеры, смены и handoff", "Уведомления и ручные сообщения", "Google Sheets, API, webhooks и базовая аналитика", "Укрепление, deploy и ручная кампания",
    ), start=4),
    *_stage_cards("S2", STAGE_2_LIST, STAGE_2_PLAN, (
        "AI-контракт и граница провайдеров", "Версионируемые AI-профили", "AI-оркестрация и backend-валидация", "Память клиента и summary", "Версионируемые знания и RAG", "Контролируемый внешний поиск", "Офферы, трекинг и postback", "Языки, голос, вложения и AI-интерфейс", "Оценка AI и сквозной gate",
    )),
    *_stage_cards("S3", STAGE_3_LIST, STAGE_3_PLAN, (
        "Модерация кандидатов в знания", "Версии и воспроизводимость", "A/B-тестирование и адаптивный темп", "Полная аналитика, расходы и выплаты", "Производительность, безопасность, восстановление и наблюдаемость", "Приёмка MVP и release runbook",
    )),
)


def _description(card: RoadmapCard, notes: str = "") -> str:
    next_step = f"\nСледующий шаг: {card.next_action}" if card.next_action else ""
    return (
        "## Сводка\n"
        f"Статус: {card.status}\n"
        f"Цель: {card.goal}{next_step}\n"
        f"План: `{card.plan_path}`\n\n"
        "## Заметки\n"
        f"{notes}"
    )


def _with_preserved_notes(description: str, card: RoadmapCard) -> str:
    marker = "## Заметки\n"
    notes = description.split(marker, 1)[1] if marker in description else ""
    return _description(card, notes)


class HttpTrelloTransport:
    def __init__(self, api_key: str, token: str) -> None:
        self._api_key = api_key
        self._token = token

    def request(self, method: str, path: str, *, params: dict[str, str] | None = None, data: dict[str, str] | None = None) -> Any:
        query = {"key": self._api_key, "token": self._token, **(params or {})}
        request = Request(f"https://api.trello.com{path}?{urlencode(query)}", data=urlencode(data).encode("utf-8") if data else None, method=method)
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
        return cls(values["TRELLO_BOARD_ID"].strip(), HttpTrelloTransport(values["TRELLO_API_KEY"].strip(), values["TRELLO_TOKEN"].strip()))

    def sync(self) -> SyncResult:
        board_id = self._resolved_board_id()
        lists = self._active_lists(board_id)
        created_lists = 0
        for name in LIST_NAMES:
            if name not in lists:
                lists[name] = self._transport.request("POST", "/1/lists", data={"idBoard": board_id, "name": name, "pos": "bottom"})
                created_lists += 1

        created_cards, updated_cards = self._sync_cards(board_id, lists)
        archived_lists = self._archive_legacy_lists(lists)
        return SyncResult(created_lists, created_cards, updated_cards, archived_lists)

    def _resolved_board_id(self) -> str:
        board = self._transport.request("GET", f"/1/boards/{self._board_id}")
        if not isinstance(board, dict) or board.get("closed", False) or not board.get("id"):
            raise TrelloRequestError("Trello returned an unavailable board")
        return str(board["id"])

    def _active_lists(self, board_id: str) -> dict[str, dict[str, Any]]:
        response = self._transport.request("GET", f"/1/boards/{board_id}/lists")
        if not isinstance(response, list):
            raise TrelloRequestError("Trello returned an invalid list response")
        return {str(item["name"]): item for item in response if isinstance(item, dict) and item.get("id") and item.get("name") and not item.get("closed", False)}

    def _sync_cards(self, board_id: str, lists: dict[str, dict[str, Any]]) -> tuple[int, int]:
        response = self._transport.request("GET", f"/1/boards/{board_id}/cards")
        if not isinstance(response, list):
            raise TrelloRequestError("Trello returned an invalid card response")
        existing = {str(item.get("name", "")).split(" ·", 1)[0]: item for item in response if isinstance(item, dict) and not item.get("closed", False)}
        created = updated = 0
        for card in ROADMAP_CARDS:
            name = f"{card.code} · {card.title}"
            found = existing.get(card.code)
            if found is None:
                self._transport.request(
                    "POST",
                    "/1/cards",
                    data={
                        "idList": str(lists[card.list_name]["id"]),
                        "name": name,
                        "desc": _description(card),
                        "dueComplete": str(card.is_complete).lower(),
                    },
                )
                created += 1
                continue
            updated_description = _with_preserved_notes(str(found.get("desc", "")), card)
            is_complete = bool(found.get("dueComplete", False))
            if updated_description != found.get("desc", "") or is_complete != card.is_complete:
                changes: dict[str, str] = {}
                if updated_description != found.get("desc", ""):
                    changes["desc"] = updated_description
                if is_complete != card.is_complete:
                    changes["dueComplete"] = str(card.is_complete).lower()
                self._transport.request("PUT", f"/1/cards/{found['id']}", data=changes)
                updated += 1
        return created, updated

    def _archive_legacy_lists(self, lists: dict[str, dict[str, Any]]) -> int:
        archived = 0
        for name in LEGACY_LIST_NAMES:
            item = lists.get(name)
            if item is not None:
                self._transport.request("PUT", f"/1/lists/{item['id']}/closed", data={"value": "true"})
                archived += 1
        return archived


def main() -> int:
    try:
        result = TrelloBoardSynchronizer.from_environment().sync()
    except (ConfigurationError, TrelloRequestError) as error:
        print(f"Trello synchronization failed: {error}", file=sys.stderr)
        return 1
    print(f"Trello roadmap synchronized: lists created={result.created_lists}, cards created={result.created_cards}, cards updated={result.updated_cards}, lists archived={result.archived_lists}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
