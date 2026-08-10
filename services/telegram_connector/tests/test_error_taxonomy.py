"""Stable, non-diagnostic Telegram error taxonomy contracts."""

import asyncio

import pytest

from telegram_connector import TelegramGatewayError, map_telegram_error
from telegram_connector.runtime.connection import AccountBlockedError, AuthorizationLostError, FloodWaitError


class PeerIdInvalidError(Exception):
    pass


class UserPrivacyRestrictedError(Exception):
    pass


class AllowPaidMessagesError(Exception):
    pass


class UnknownTelegramFailure(Exception):
    pass


@pytest.mark.parametrize(
    "source,expected",
    [
        (PeerIdInvalidError("phone +15551234567"), "invalid_peer"),
        (UserPrivacyRestrictedError("raw privacy detail"), "privacy_restricted"),
        (AllowPaidMessagesError("payment token"), "paid_message_required"),
        (FloodWaitError(3), "rate_limited"),
        (AuthorizationLostError(), "authorization_lost"),
        (AccountBlockedError(), "account_blocked"),
        (TimeoutError("raw timeout detail"), "timeout"),
        (UnknownTelegramFailure("raw telegram content"), "telegram_unknown"),
    ],
)
def test_maps_telegram_failures_to_stable_safe_codes(source, expected):
    """Using upstream exception text would make API errors unsafe and unstable across client versions."""
    mapped = map_telegram_error(source)
    assert mapped == expected

    safe = TelegramGatewayError(mapped)
    assert safe.code == expected
    assert str(source) not in str(safe)


def test_error_taxonomy_is_not_derived_from_exception_message_text():
    """Matching message text would break across Telegram client versions and expose raw details."""
    class ArbitraryError(Exception):
        pass

    raw = ArbitraryError("FloodWaitError privacy payment phone +15551234567")
    assert map_telegram_error(raw) == "telegram_unknown"
