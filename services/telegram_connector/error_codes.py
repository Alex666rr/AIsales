"""Stable error codes for the injected Telegram message boundary."""

import asyncio
from typing import Literal

from telegram_connector.runtime.connection import AccountBlockedError, AuthorizationLostError, FloodWaitError


TelegramErrorCode = Literal[
    "invalid_peer",
    "privacy_restricted",
    "paid_message_required",
    "rate_limited",
    "authorization_lost",
    "account_blocked",
    "timeout",
    "telegram_unknown",
    "connection_inactive",
]


class TelegramGatewayError(RuntimeError):
    """A stable public failure that intentionally excludes upstream exception details."""

    def __init__(self, code: TelegramErrorCode) -> None:
        self.code = code
        super().__init__(f"telegram gateway error: {code}")


class TelegramAdapterError(Exception):
    """Explicit adapter translation signal; never use a third-party class name as policy."""

    code: TelegramErrorCode


class InvalidPeerAdapterError(TelegramAdapterError):
    code = "invalid_peer"


class PrivacyRestrictedAdapterError(TelegramAdapterError):
    code = "privacy_restricted"


class PaidMessageRequiredAdapterError(TelegramAdapterError):
    code = "paid_message_required"


def map_telegram_error(error: BaseException) -> TelegramErrorCode:
    """Map only exception type identity; source messages never affect public output."""
    if isinstance(error, FloodWaitError):
        return "rate_limited"
    if isinstance(error, AuthorizationLostError):
        return "authorization_lost"
    if isinstance(error, AccountBlockedError):
        return "account_blocked"
    if isinstance(error, TelegramAdapterError):
        return error.code
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    return "telegram_unknown"
