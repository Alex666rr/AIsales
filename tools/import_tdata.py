"""Import one Telegram Desktop Portable tdata folder without uploading raw tdata.

Run locally with the project dependencies installed.  The source tdata and the
Desktop passcode stay on this computer; Railway receives only a one-time,
ticket-encrypted canonical Telethon session.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from getpass import getpass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import UUID

from telegram_connector.importers.tdata.handoff import encrypt_handoff
from telegram_connector.importers.tdata.parser import parse_tdata
from telegram_connector.importers.tdata.preflight import prepare_tdata_copy
from telegram_connector.importers.tdata.telethon import to_telethon_string


_SESSION_PREFIX = b"TELETHON_STRING_SESSION\x00\x01"


def main() -> None:
    parser = argparse.ArgumentParser(description="Locally import one Telegram Desktop tdata folder")
    parser.add_argument("source", type=Path, help="Local Telegram Desktop tdata folder")
    parser.add_argument("--api-url", default=os.environ.get("AISALES_API_URL"))
    parser.add_argument("--api-id", default=os.environ.get("TELEGRAM_API_ID"), type=int)
    parser.add_argument("--api-hash", default=os.environ.get("TELEGRAM_API_HASH"))
    parser.add_argument("--owner-token", default=os.environ.get("PLATFORM_OWNER_TOKEN"))
    arguments = parser.parse_args()

    _require_configuration(arguments)
    passcode = getpass("Telegram Desktop local passcode (Enter if none): ")
    with tempfile.TemporaryDirectory(prefix="ai-sales-tdata-") as directory:
        snapshot = prepare_tdata_copy(arguments.source, Path(directory) / "tdata", max_bytes=64 * 1024 * 1024)
        parsed = parse_tdata(snapshot, passcode=passcode or None)
        session_value = to_telethon_string(parsed)
        telegram_user_id = asyncio.run(
            _verify_session(arguments.api_id, arguments.api_hash, session_value, parsed.user_id)
        )
        ticket = _post_json(arguments.api_url, "/telegram/connections/tdata/tickets", {}, arguments.owner_token)
        envelope = encrypt_handoff(
            ticket_id=UUID(ticket["ticket_id"]),
            server_public_key=ticket["public_key"],
            telegram_user_id=telegram_user_id,
            session_payload=_SESSION_PREFIX + session_value.encode("ascii"),
        )
        result = _post_json(
            arguments.api_url,
            f"/telegram/connections/tdata/tickets/{ticket['ticket_id']}/handoff",
            {
                "client_public_key": envelope.client_public_key,
                "nonce": envelope.nonce,
                "ciphertext": envelope.ciphertext,
            },
            arguments.owner_token,
        )
    print(f"Imported Telegram account {result['telegram_user_id']} as {result['account_id']} ({result['state']}).")


def _require_configuration(arguments: argparse.Namespace) -> None:
    if not all((arguments.api_url, arguments.api_id, arguments.api_hash, arguments.owner_token)):
        raise SystemExit("Set AISALES_API_URL, TELEGRAM_API_ID, TELEGRAM_API_HASH and PLATFORM_OWNER_TOKEN locally.")
    parsed = urlparse(arguments.api_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SystemExit("AISALES_API_URL must use HTTPS.")


async def _verify_session(api_id: int, api_hash: str, session_value: str, expected_user_id: int) -> int:
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(session_value), api_id, api_hash, receive_updates=False)
        try:
            await client.connect()
            identity = await client.get_me()
            actual_user_id = getattr(identity, "id", None)
            if type(actual_user_id) is not int or actual_user_id <= 0 or actual_user_id != expected_user_id:
                raise ValueError
            return actual_user_id
        finally:
            await client.disconnect()
    except Exception:
        raise SystemExit("This tdata authorization could not be verified locally.") from None


def _post_json(api_url: str, path: str, payload: dict[str, str], owner_token: str) -> dict[str, str]:
    request = Request(
        api_url.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {owner_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            document = json.loads(response.read(64 * 1024).decode("utf-8"))
        if not isinstance(document, dict):
            raise ValueError
        return document
    except Exception:
        raise SystemExit("The secure tdata handoff was rejected or unavailable.") from None


if __name__ == "__main__":
    main()
