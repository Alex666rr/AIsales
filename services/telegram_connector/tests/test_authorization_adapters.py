"""Network-free acceptance tests for Telegram authorization adapters."""

import asyncio
import io
import tarfile
import zipfile
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from telegram_connector.adapters.bot import BotAdapter
from telegram_connector.adapters.phone import PhoneAdapter
from telegram_connector.adapters.qr import QRAdapter
from telegram_connector.adapters.registry import AdapterRegistry
from telegram_connector.adapters.tdata import TDataAdapter
from telegram_connector.adapters.telethon_session import (
    TelethonFileAdapter,
    TelethonStringAdapter,
)
from telegram_connector.adapters.base import SessionMaterial


class InvalidCode(Exception):
    pass


class PasswordRequired(Exception):
    pass


class QrExpired(Exception):
    pass


class FakeUserClient:
    """Offline client with deliberately secret-looking failures."""

    def __init__(self, *, code_result: object = (41, "alice"), qr_result: object = (42, "bob")) -> None:
        self.code_result = code_result
        self.qr_result = qr_result
        self.codes_requested: list[str] = []

    async def request_code(self, phone: str) -> str:
        self.codes_requested.append(phone)
        return "raw-server-code-hash"

    async def sign_in(self, phone: str, code: str) -> tuple[int, str]:
        if self.code_result == "invalid":
            raise InvalidCode(f"bad {code} for {phone}")
        if self.code_result == "2fa":
            raise PasswordRequired(f"password required for {phone}")
        return self.code_result  # type: ignore[return-value]

    async def check_password(self, password: str) -> tuple[int, str]:
        if password == "bad":
            raise InvalidCode(f"bad password {password}")
        return (41, "alice")

    async def request_qr(self) -> str:
        return "qr-login-token"

    async def complete_qr(self, token: str) -> tuple[int, str]:
        if self.qr_result == "expired":
            raise QrExpired(f"expired {token}")
        return self.qr_result  # type: ignore[return-value]


class FakeConverter:
    async def convert_tdata(self, data: bytes) -> bytes:
        if data.endswith(b"FAIL"):
            raise RuntimeError("raw archive data must not escape")
        return b"user-session"

    async def convert_telethon_file(self, data: bytes) -> bytes:
        return b"file-session"

    async def convert_telethon_string(self, data: bytes) -> bytes:
        return b"string-session"


class FakeBotApi:
    async def get_me(self, token: str) -> tuple[int, str]:
        if token != "123:valid-token":
            raise RuntimeError(f"telegram rejected {token}")
        return (99, "sales_bot")


def run(coro):
    return asyncio.run(coro)


def tdata_payload(contents: bytes = b"payload") -> bytes:
    return b"TDATA\x00\x01" + contents


def telethon_file_payload(contents: bytes = b"payload") -> bytes:
    return b"TELETHON_FILE\x00\x01" + contents


def telethon_string_payload(contents: bytes = b"payload") -> bytes:
    return b"TELETHON_STRING\x00\x01" + contents


def test_phone_code_flow_authorizes_without_returning_phone_code_or_client_secret():
    """Removing server-side challenge data or redaction must make this flow unsafe."""
    client = FakeUserClient()
    adapter = PhoneAdapter(lambda: client, ttl=timedelta(minutes=2))

    start = run(adapter.start("+15551234567"))
    result = run(adapter.submit_code(start.challenge_id, "12345"))

    assert start.state == "code_sent"
    assert result.state == "authorized"
    assert result.safe_message == "Authorization completed."
    assert result.model_dump() == {
        "state": "authorized",
        "challenge_id": start.challenge_id,
        "expires_at": result.expires_at,
        "safe_message": "Authorization completed.",
    }
    assert "+15551234567" not in repr(result)
    assert "12345" not in repr(result)
    assert "raw-server-code-hash" not in repr(result)


def test_phone_invalid_code_is_a_safe_failed_state_without_exception_detail():
    """Returning raw client errors would expose the rejected code and phone number."""
    adapter = PhoneAdapter(lambda: FakeUserClient(code_result="invalid"))
    start = run(adapter.start("+15551234567"))

    result = run(adapter.submit_code(start.challenge_id, "sensitive-code"))

    assert result.state == "failed"
    assert result.safe_message == "Authorization could not be completed."
    assert "sensitive-code" not in repr(result)
    assert "+15551234567" not in repr(result)


def test_phone_2fa_is_bound_to_one_challenge_and_cannot_replay_code():
    """Dropping challenge state checks would permit a consumed code challenge to be replayed."""
    adapter = PhoneAdapter(lambda: FakeUserClient(code_result="2fa"))
    start = run(adapter.start("+15551234567"))
    password_step = run(adapter.submit_code(start.challenge_id, "12345"))
    replay = run(adapter.submit_code(start.challenge_id, "12345"))
    authorized = run(adapter.submit_password(start.challenge_id, "good-password"))

    assert password_step.state == "needs_2fa"
    assert replay.state == "failed"
    assert authorized.state == "authorized"
    assert "good-password" not in repr(authorized)


def test_expired_phone_challenge_never_calls_client_or_accepts_code():
    """Removing expiration enforcement would call Telegram with an expired challenge."""
    client = FakeUserClient()
    adapter = PhoneAdapter(lambda: client, now=lambda: datetime(2026, 8, 7, tzinfo=UTC), ttl=timedelta(0))
    start = run(adapter.start("+15551234567"))

    result = run(adapter.submit_code(start.challenge_id, "12345"))

    assert result.state == "expired"
    assert client.codes_requested == ["+15551234567"]


def test_client_factory_failure_is_normalized_without_secret_exception_text():
    """Allowing factory failures through would leak configured Telegram credentials."""
    def broken_factory():
        raise RuntimeError("api_hash=raw-api-secret")

    phone = PhoneAdapter(broken_factory)
    qr = QRAdapter(broken_factory)

    phone_result = run(phone.start("+15551234567"))
    qr_result = run(qr.start())

    assert phone_result.state == "failed"
    assert qr_result.state == "failed"
    assert "raw-api-secret" not in repr(phone_result)
    assert "raw-api-secret" not in repr(qr_result)


def test_qr_expiry_requires_a_fresh_challenge_before_retrying():
    """Reusing an expired QR token would authorize a stale, replayable QR challenge."""
    client = FakeUserClient(qr_result="expired")
    adapter = QRAdapter(lambda: client)
    first = run(adapter.start())
    expired = run(adapter.complete(first.challenge_id))
    replay = run(adapter.complete(first.challenge_id))

    assert first.state == "code_sent"
    assert expired.state == "expired"
    assert replay.state == "failed"


def test_import_adapters_convert_only_registered_signed_formats():
    """Accepting unsigned bytes would let arbitrary uploads enter session conversion."""
    converter = FakeConverter()
    tdata = TDataAdapter(converter)
    file_adapter = TelethonFileAdapter(converter)
    string_adapter = TelethonStringAdapter(converter)

    assert run(tdata.convert(SessionMaterial(adapter="tdata", payload=tdata_payload(), credentials={}))) == b"user-session"
    assert run(file_adapter.convert(SessionMaterial(adapter="telethon_file", payload=telethon_file_payload(), credentials={}))) == b"file-session"
    assert run(string_adapter.convert(SessionMaterial(adapter="telethon_string", payload=telethon_string_payload(), credentials={}))) == b"string-session"
    with pytest.raises(ValueError, match="^unsupported session import$"):
        run(tdata.convert(SessionMaterial(adapter="tdata", payload=b"unsigned", credentials={})))


@pytest.mark.parametrize("bad_payload", [b"TDATA\x00\x02payload", b"TDATA\x00", b"TDATA\x00\x01" + b"x" * 65])
def test_tdata_rejects_incompatible_or_oversized_raw_payloads(bad_payload):
    """Relaxing version or size checks would process unsupported or bomb-like uploads."""
    adapter = TDataAdapter(FakeConverter(), max_uncompressed_bytes=64)

    with pytest.raises(ValueError, match="^unsupported session import$") as failure:
        run(adapter.convert(SessionMaterial(adapter="tdata", payload=bad_payload, credentials={})))

    assert "TDATA" not in str(failure.value)


def test_tdata_rejects_traversal_and_symlink_entries_before_archive_conversion():
    """Skipping archive member checks would allow unsafe archive paths into conversion."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape", b"x")
    adapter = TDataAdapter(FakeConverter())

    with pytest.raises(ValueError, match="^unsupported session import$"):
        run(adapter.convert(SessionMaterial(adapter="tdata", payload=archive.getvalue(), credentials={})))

    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as bundle:
        entry = tarfile.TarInfo("linked")
        entry.type = tarfile.SYMTYPE
        entry.linkname = "../escape"
        bundle.addfile(entry)
    with pytest.raises(ValueError, match="^unsupported session import$"):
        run(adapter.convert(SessionMaterial(adapter="tdata", payload=tar_buffer.getvalue(), credentials={})))


def test_bot_token_is_checked_by_injected_api_and_is_never_serialized():
    """Skipping Bot API validation would accept an invalid token as an authorized bot."""
    adapter = BotAdapter(FakeBotApi())
    material = SessionMaterial(adapter="bot", payload=b"", credentials={"token": "123:valid-token"})
    result = run(adapter.probe(material))
    converted = run(adapter.convert(material))

    assert result.state == "authorized"
    assert result.telegram_user_id == 99
    assert result.username == "sales_bot"
    assert result.capabilities == frozenset({"bot_api"})
    assert converted.startswith(b"BOT_API_SESSION\x00\x01")
    assert "123:valid-token" not in repr(result)
    assert "123:valid-token" not in str(converted)


def test_invalid_bot_token_normalizes_client_error_and_never_becomes_user_session():
    """Leaking client failures or using MTProto session headers breaks bot isolation."""
    adapter = BotAdapter(FakeBotApi())
    material = SessionMaterial(adapter="bot", payload=b"", credentials={"token": "bad-token"})

    result = run(adapter.probe(material))
    with pytest.raises(ValueError, match="^bot authorization failed$") as failure:
        run(adapter.convert(material))

    assert result.state == "invalid"
    assert result.error_code == "invalid_bot_token"
    assert "bad-token" not in str(failure.value)


def test_registry_has_exactly_the_six_public_adapter_names_and_rejects_unknown():
    """Changing the registry mapping would make a required adapter unreachable or misrouted."""
    converter = FakeConverter()
    registry = AdapterRegistry(
        phone=PhoneAdapter(lambda: FakeUserClient()),
        qr=QRAdapter(lambda: FakeUserClient()),
        tdata=TDataAdapter(converter),
        telethon_file=TelethonFileAdapter(converter),
        telethon_string=TelethonStringAdapter(converter),
        bot=BotAdapter(FakeBotApi()),
    )

    assert registry.names == (
        "phone", "qr", "tdata", "telethon_file", "telethon_string", "bot"
    )
    assert registry.get("phone").name == "phone"
    with pytest.raises(ValueError, match="^unsupported authorization adapter$"):
        registry.get("other")
