"""Opt-in live proof for project-owned Telegram test accounts only.

The harness lives outside this repository so its account fixtures, sessions, and
credentials remain ignored.  This test is intentionally skipped unless an
operator explicitly enables it and supplies that owned-account harness.
"""

import importlib
import os
from uuid import uuid4

import pytest


if os.environ.get("RUN_TELEGRAM_LIVE_TESTS") != "1":
    pytestmark = pytest.mark.skip(
        reason="live Telegram roundtrip is disabled; set RUN_TELEGRAM_LIVE_TESTS=1 for owned test accounts"
    )


def _live_harness():
    reference = os.environ.get("TELEGRAM_LIVE_ROUNDTRIP_FACTORY")
    if not reference or ":" not in reference:
        pytest.skip("owned live fixture is not configured via TELEGRAM_LIVE_ROUNDTRIP_FACTORY")
    module_name, factory_name = reference.split(":", 1)
    factory = getattr(importlib.import_module(module_name), factory_name)
    return factory()


def test_owned_accounts_roundtrip_restart_reply_and_archive_sender_session():
    """The external harness must send/receive/restart with owned accounts and archive in finally."""
    async def scenario() -> None:
        harness = _live_harness()
        marker = f"stage0-owned-test-{uuid4()}"
        try:
            await harness.send_from_sender(marker)
            assert await harness.receive_at_recipient(marker) is True
            await harness.restart_connector()
            reply = f"{marker}-reply"
            await harness.send_from_recipient(reply)
            assert await harness.receive_at_sender(reply) is True

            rows = await harness.compatibility_rows()
            combinations = {(row.adapter, row.adapter_version, row.proxy_id) for row in rows}
            assert len(rows) == len(combinations)
        finally:
            await harness.archive_sender_session()

    import asyncio

    asyncio.run(scenario())
