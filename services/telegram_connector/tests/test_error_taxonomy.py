"""Stable, non-diagnostic Telegram error taxonomy contracts."""

import asyncio

import pytest

from telegram_connector import (
    InvalidPeerAdapterError,
    PaidMessageRequiredAdapterError,
    PrivacyRestrictedAdapterError,
    TelegramGatewayError,
    map_telegram_error,
)
from telegram_connector.runtime.connection import AccountBlockedError, AuthorizationLostError, FloodWaitError


class UnknownTelegramFailure(Exception):
    pass


@pytest.mark.parametrize(
    "source,expected",
    [
        (InvalidPeerAdapterError("phone +15551234567"), "invalid_peer"),
        (PrivacyRestrictedAdapterError("raw privacy detail"), "privacy_restricted"),
        (PaidMessageRequiredAdapterError("payment token"), "paid_message_required"),
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


def test_same_name_unrelated_adapter_exception_is_unknown():
    """Class-name matching could let an unrelated dependency select a Telegram error code."""
    PeerIdInvalidError = type("PeerIdInvalidError", (Exception,), {})
    assert map_telegram_error(PeerIdInvalidError("raw detail")) == "telegram_unknown"
