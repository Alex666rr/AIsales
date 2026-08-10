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


_CLASS_CODES: dict[str, TelegramErrorCode] = {
    "PeerIdInvalidError": "invalid_peer",
    "PeerIdInvalid": "invalid_peer",
    "UsernameInvalidError": "invalid_peer",
    "UsernameNotOccupiedError": "invalid_peer",
    "UserPrivacyRestrictedError": "privacy_restricted",
    "ChatWriteForbiddenError": "privacy_restricted",
    "AllowPaidMessagesError": "paid_message_required",
    "FloodWaitError": "rate_limited",
    "AuthKeyUnregisteredError": "authorization_lost",
    "SessionRevokedError": "authorization_lost",
    "UserDeactivatedError": "account_blocked",
    "UserDeactivatedBanError": "account_blocked",
}


def map_telegram_error(error: BaseException) -> TelegramErrorCode:
    """Map only exception type identity; source messages never affect public output."""
    if isinstance(error, FloodWaitError):
        return "rate_limited"
    if isinstance(error, AuthorizationLostError):
        return "authorization_lost"
    if isinstance(error, AccountBlockedError):
        return "account_blocked"
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    return _CLASS_CODES.get(error.__class__.__name__, "telegram_unknown")
