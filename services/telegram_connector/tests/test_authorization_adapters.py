"""Network-free acceptance tests for Telegram authorization adapters."""

import asyncio
import io
import stat
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


pytestmark = pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")


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
    def __init__(self) -> None:
        self.calls = 0

    async def convert_tdata(self, data: bytes) -> bytes:
        self.calls += 1
        if data.endswith(b"FAIL"):
            raise RuntimeError("raw archive data must not escape")
        return b"user-session"

    async def convert_telethon_file(self, data: bytes) -> bytes:
        return b"file-session"

    async def convert_telethon_string(self, data: bytes) -> bytes:
        return b"string-session"


class FakeBotApi:
    def __init__(self, identity: object = (99, "sales_bot")) -> None:
        self.identity = identity

    async def get_me(self, token: str) -> tuple[int, str]:
        if token != "123:valid-token":
            raise RuntimeError(f"telegram rejected {token}")
        return self.identity  # type: ignore[return-value]


OWNER_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OWNER_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


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

    start = run(adapter.start("+15551234567", OWNER_A))
    result = run(adapter.submit_code(start.challenge_id, OWNER_A, "12345"))

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
    start = run(adapter.start("+15551234567", OWNER_A))

    result = run(adapter.submit_code(start.challenge_id, OWNER_A, "sensitive-code"))

    assert result.state == "failed"
    assert result.safe_message == "Authorization could not be completed."
    assert "sensitive-code" not in repr(result)
    assert "+15551234567" not in repr(result)


def test_phone_2fa_is_bound_to_one_challenge_and_cannot_replay_code():
    """Dropping challenge state checks would permit a consumed code challenge to be replayed."""
    adapter = PhoneAdapter(lambda: FakeUserClient(code_result="2fa"))
    start = run(adapter.start("+15551234567", OWNER_A))
    password_step = run(adapter.submit_code(start.challenge_id, OWNER_A, "12345"))
    replay = run(adapter.submit_code(start.challenge_id, OWNER_A, "12345"))
    authorized = run(adapter.submit_password(start.challenge_id, OWNER_A, "good-password"))

    assert password_step.state == "needs_2fa"
    assert replay.state == "failed"
    assert authorized.state == "authorized"
    assert "good-password" not in repr(authorized)


def test_expired_phone_challenge_never_calls_client_or_accepts_code():
    """Removing expiration enforcement would call Telegram with an expired challenge."""
    client = FakeUserClient()
    adapter = PhoneAdapter(lambda: client, now=lambda: datetime(2026, 8, 7, tzinfo=UTC), ttl=timedelta(0))
    start = run(adapter.start("+15551234567", OWNER_A))

    result = run(adapter.submit_code(start.challenge_id, OWNER_A, "12345"))

    assert result.state == "expired"
    assert client.codes_requested == ["+15551234567"]


def test_client_factory_failure_is_normalized_without_secret_exception_text():
    """Allowing factory failures through would leak configured Telegram credentials."""
    def broken_factory():
        raise RuntimeError("api_hash=raw-api-secret")

    phone = PhoneAdapter(broken_factory)
    qr = QRAdapter(broken_factory)

    phone_result = run(phone.start("+15551234567", OWNER_A))
    qr_result = run(qr.start(OWNER_A))

    assert phone_result.state == "failed"
    assert qr_result.state == "failed"
    assert "raw-api-secret" not in repr(phone_result)
    assert "raw-api-secret" not in repr(qr_result)


def test_qr_expiry_requires_a_fresh_challenge_before_retrying():
    """Reusing an expired QR token would authorize a stale, replayable QR challenge."""
    client = FakeUserClient(qr_result="expired")
    adapter = QRAdapter(lambda: client)
    first = run(adapter.start(OWNER_A))
    expired = run(adapter.complete(first.challenge_id, OWNER_A))
    replay = run(adapter.complete(first.challenge_id, OWNER_A))

    assert first.state == "code_sent"
    assert expired.state == "expired"
    assert replay.state == "failed"


def test_phone_continuations_reject_a_different_authenticated_owner():
    """Dropping private owner checks would let another principal spend a challenge."""
    client = FakeUserClient(code_result="2fa")
    adapter = PhoneAdapter(lambda: client)
    start = run(adapter.start("+15551234567", OWNER_A))

    wrong_code = run(adapter.submit_code(start.challenge_id, OWNER_B, "12345"))
    needs_2fa = run(adapter.submit_code(start.challenge_id, OWNER_A, "12345"))
    wrong_password = run(adapter.submit_password(start.challenge_id, OWNER_B, "password"))
    authorized = run(adapter.submit_password(start.challenge_id, OWNER_A, "password"))

    assert wrong_code.state == "failed"
    assert needs_2fa.state == "needs_2fa"
    assert wrong_password.state == "failed"
    assert authorized.state == "authorized"
    assert str(OWNER_A) not in repr(authorized)
    assert str(OWNER_B) not in repr(wrong_code)


def test_qr_completion_rejects_a_different_authenticated_owner():
    """A QR token bound only to an ID could be consumed by any logged-in user."""
    client = FakeUserClient()
    adapter = QRAdapter(lambda: client)
    start = run(adapter.start(OWNER_A))

    wrong_owner = run(adapter.complete(start.challenge_id, OWNER_B))
    authorized = run(adapter.complete(start.challenge_id, OWNER_A))

    assert wrong_owner.state == "failed"
    assert authorized.state == "authorized"


class BlockingUserClient(FakeUserClient):
    def __init__(self, *, password_required: bool = False, block: str, advance_clock=None) -> None:
        super().__init__(code_result="2fa" if password_required else (41, "alice"))
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.sign_in_calls = 0
        self.password_calls = 0
        self.qr_calls = 0
        self.block = block
        self.advance_clock = advance_clock

    async def sign_in(self, phone: str, code: str) -> tuple[int, str]:
        self.sign_in_calls += 1
        if self.block == "code":
            self.started.set()
            await self.release.wait()
            if self.advance_clock is not None:
                self.advance_clock()
        return await super().sign_in(phone, code)

    async def check_password(self, password: str) -> tuple[int, str]:
        self.password_calls += 1
        if self.block == "password":
            self.started.set()
            await self.release.wait()
            if self.advance_clock is not None:
                self.advance_clock()
        return await super().check_password(password)

    async def complete_qr(self, token: str) -> tuple[int, str]:
        self.qr_calls += 1
        if self.block == "qr":
            self.started.set()
            await self.release.wait()
            if self.advance_clock is not None:
                self.advance_clock()
        return await super().complete_qr(token)


def test_phone_claims_code_before_await_so_concurrent_submissions_call_client_once():
    """Leaving a challenge claim until after await would authorize concurrent code submissions."""
    async def scenario():
        client = BlockingUserClient(block="code")
        adapter = PhoneAdapter(lambda: client)
        start = await adapter.start("+15551234567", OWNER_A)
        first = asyncio.create_task(adapter.submit_code(start.challenge_id, OWNER_A, "12345"))
        await client.started.wait()
        second = await adapter.submit_code(start.challenge_id, OWNER_A, "67890")
        client.release.set()
        return await first, second, client

    first, second, client = run(scenario())

    assert client.sign_in_calls == 1
    assert {first.state, second.state} == {"authorized", "failed"}


def test_phone_claims_password_before_await_so_concurrent_submissions_call_client_once():
    """Leaving a 2FA challenge unclaimed would permit duplicate password authorization calls."""
    async def scenario():
        client = BlockingUserClient(password_required=True, block="password")
        adapter = PhoneAdapter(lambda: client)
        start = await adapter.start("+15551234567", OWNER_A)
        assert (await adapter.submit_code(start.challenge_id, OWNER_A, "12345")).state == "needs_2fa"
        first = asyncio.create_task(adapter.submit_password(start.challenge_id, OWNER_A, "password"))
        await client.started.wait()
        second = await adapter.submit_password(start.challenge_id, OWNER_A, "password")
        client.release.set()
        return await first, second, client

    first, second, client = run(scenario())

    assert client.password_calls == 1
    assert {first.state, second.state} == {"authorized", "failed"}


def test_qr_claims_completion_before_await_so_concurrent_submissions_call_client_once():
    """A QR completion race must not call the client twice for one token."""
    async def scenario():
        client = BlockingUserClient(block="qr")
        adapter = QRAdapter(lambda: client)
        start = await adapter.start(OWNER_A)
        first = asyncio.create_task(adapter.complete(start.challenge_id, OWNER_A))
        await client.started.wait()
        second = await adapter.complete(start.challenge_id, OWNER_A)
        client.release.set()
        return await first, second, client

    first, second, client = run(scenario())

    assert client.qr_calls == 1
    assert {first.state, second.state} == {"authorized", "failed"}


@pytest.mark.parametrize("flow", ["code", "password", "qr"])
def test_clock_crossing_during_await_never_returns_authorized(flow):
    """Only checking expiry before an await would authorize a challenge that elapsed in flight."""
    async def scenario():
        clock = [datetime(2026, 8, 7, tzinfo=UTC)]
        client = BlockingUserClient(
            password_required=flow == "password",
            block=flow,
            advance_clock=lambda: clock.__setitem__(0, clock[0] + timedelta(minutes=2)),
        )
        if flow == "qr":
            adapter = QRAdapter(lambda: client, ttl=timedelta(minutes=1), now=lambda: clock[0])
            start = await adapter.start(OWNER_A)
            task = asyncio.create_task(adapter.complete(start.challenge_id, OWNER_A))
        else:
            adapter = PhoneAdapter(lambda: client, ttl=timedelta(minutes=1), now=lambda: clock[0])
            start = await adapter.start("+15551234567", OWNER_A)
            if flow == "password":
                assert (await adapter.submit_code(start.challenge_id, OWNER_A, "12345")).state == "needs_2fa"
                task = asyncio.create_task(adapter.submit_password(start.challenge_id, OWNER_A, "password"))
            else:
                task = asyncio.create_task(adapter.submit_code(start.challenge_id, OWNER_A, "12345"))
        await client.started.wait()
        client.release.set()
        return await task

    assert run(scenario()).state == "expired"


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


def test_tdata_streaming_tar_rejects_declared_bomb_and_excessive_members_before_conversion():
    """Eager TAR enumeration or extraction would process an oversized/bomb-like archive."""
    converter = FakeConverter()
    adapter = TDataAdapter(converter, max_compressed_bytes=4096, max_uncompressed_bytes=64)

    oversized = io.BytesIO()
    with tarfile.open(fileobj=oversized, mode="w:gz") as bundle:
        bundle.addfile(tarfile.TarInfo("tdata/session.bin"), io.BytesIO())
        member = tarfile.TarInfo("too-large")
        member.size = 65
        bundle.addfile(member, io.BytesIO(b"x" * 65))
    with pytest.raises(ValueError, match="^unsupported session import$"):
        run(adapter.convert(SessionMaterial(adapter="tdata", payload=oversized.getvalue(), credentials={})))

    excessive = io.BytesIO()
    with tarfile.open(fileobj=excessive, mode="w:gz") as bundle:
        for number in range(17):
            member = tarfile.TarInfo(f"member-{number}")
            member.size = 1
            bundle.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="^unsupported session import$"):
        run(adapter.convert(SessionMaterial(adapter="tdata", payload=excessive.getvalue(), credentials={})))

    assert converter.calls == 0


@pytest.mark.parametrize("format_name", ["pax", "gnu"])
def test_tdata_caps_gzip_tar_metadata_before_materializing_or_conversion(format_name):
    """Ignoring PAX/GNU metadata would let a tiny gzip archive expand before member checks."""
    archive = io.BytesIO()
    if format_name == "pax":
        tar_format = tarfile.PAX_FORMAT
        headers = {"comment": "PAX-METADATA-SENTINEL-" + "x" * 4096}
        name = "tdata/session.bin"
    else:
        tar_format = tarfile.GNU_FORMAT
        headers = None
        name = "gnu-longname-" + "x" * 4096
    with tarfile.open(fileobj=archive, mode="w:gz", format=tar_format, pax_headers=headers) as bundle:
        member = tarfile.TarInfo(name)
        payload = tdata_payload()
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))
    converter = FakeConverter()
    adapter = TDataAdapter(converter, max_compressed_bytes=4096, max_uncompressed_bytes=1024)

    with pytest.raises(ValueError, match="^unsupported session import$") as failure:
        run(adapter.convert(SessionMaterial(adapter="tdata", payload=archive.getvalue(), credentials={})))

    assert converter.calls == 0
    assert "PAX-METADATA-SENTINEL" not in str(failure.value)
    assert "gnu-longname" not in str(failure.value)


def _mutate_zip_field(data: bytes, signature: bytes, field_offset: int, value: int) -> bytes:
    """Produce a malformed external ZIP fixture without relying on writer support."""
    result = bytearray(data)
    start = 0
    while True:
        index = result.find(signature, start)
        if index < 0:
            break
        result[index + field_offset : index + field_offset + 2] = value.to_bytes(2, "little")
        start = index + len(signature)
    return bytes(result)


def _mutate_first_zip_field(data: bytes, signature: bytes, field_offset: int, value: int) -> bytes:
    """Mutate only the directory record, leaving the valid session record untouched."""
    result = bytearray(data)
    index = result.find(signature)
    assert index >= 0
    result[index + field_offset : index + field_offset + 2] = value.to_bytes(2, "little")
    return bytes(result)


@pytest.mark.parametrize("kind", ["encrypted", "unsupported-compression"])
def test_tdata_rejects_zip_encryption_and_unknown_compression_without_detail_leaks(kind):
    """Deferring unsupported ZIP features to extraction would leak member errors or details."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("tdata/session.bin", tdata_payload())
    if kind == "encrypted":
        payload = _mutate_zip_field(archive.getvalue(), b"PK\x03\x04", 6, 1)
        payload = _mutate_zip_field(payload, b"PK\x01\x02", 8, 1)
    else:
        payload = _mutate_zip_field(archive.getvalue(), b"PK\x03\x04", 8, 99)
        payload = _mutate_zip_field(payload, b"PK\x01\x02", 10, 99)
    converter = FakeConverter()

    with pytest.raises(ValueError, match="^unsupported session import$") as failure:
        run(TDataAdapter(converter).convert(SessionMaterial(adapter="tdata", payload=payload, credentials={})))

    assert converter.calls == 0
    assert "session.bin" not in str(failure.value)


@pytest.mark.parametrize("kind", ["encrypted", "unsupported-compression"])
def test_tdata_rejects_directory_zip_features_before_accepting_a_valid_session(kind):
    """Checking directory features after continue would bypass ZIP capability validation."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("tdata/", b"")
        bundle.writestr("tdata/session.bin", tdata_payload())
    if kind == "encrypted":
        payload = _mutate_first_zip_field(archive.getvalue(), b"PK\x03\x04", 6, 1)
        payload = _mutate_first_zip_field(payload, b"PK\x01\x02", 8, 1)
    else:
        payload = _mutate_first_zip_field(archive.getvalue(), b"PK\x03\x04", 8, 99)
        payload = _mutate_first_zip_field(payload, b"PK\x01\x02", 10, 99)
    converter = FakeConverter()

    with pytest.raises(ValueError, match="^unsupported session import$"):
        run(TDataAdapter(converter).convert(SessionMaterial(adapter="tdata", payload=payload, credentials={})))

    assert converter.calls == 0


@pytest.mark.parametrize("entry_type", [stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK])
def test_tdata_rejects_every_non_regular_zip_entry_before_conversion(entry_type):
    """Checking only symlinks would accept device or FIFO ZIP entries."""
    archive = io.BytesIO()
    entry = zipfile.ZipInfo("tdata/session.bin")
    entry.external_attr = (entry_type | 0o644) << 16
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(entry, tdata_payload())
    converter = FakeConverter()

    with pytest.raises(ValueError, match="^unsupported session import$"):
        run(TDataAdapter(converter).convert(SessionMaterial(adapter="tdata", payload=archive.getvalue(), credentials={})))

    assert converter.calls == 0


@pytest.mark.parametrize(
    "names",
    [
        ("tdata/session.bin", "tdata/session.bin"),
        ("tdata/", "tdata/"),
        ("tdata", "tdata/"),
    ],
)
def test_tdata_rejects_duplicate_normalized_zip_names_and_file_directory_collisions(names):
    """Permitting duplicate/colliding paths makes the selected archive payload ambiguous."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in names:
            bundle.writestr(name, tdata_payload() if not name.endswith("/") else b"")
    converter = FakeConverter()

    with pytest.raises(ValueError, match="^unsupported session import$"):
        run(TDataAdapter(converter).convert(SessionMaterial(adapter="tdata", payload=archive.getvalue(), credentials={})))

    assert converter.calls == 0


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


@pytest.mark.parametrize("identity", [(True, "sales_bot"), (0, "sales_bot"), (-1, "sales_bot")])
def test_bot_rejects_non_positive_or_boolean_identity_ids(identity):
    """Accepting bool, zero, or negative IDs would create invalid bot session identifiers."""
    adapter = BotAdapter(FakeBotApi(identity))
    material = SessionMaterial(adapter="bot", payload=b"", credentials={"token": "123:valid-token"})

    assert run(adapter.probe(material)).state == "invalid"
    with pytest.raises(ValueError, match="^bot authorization failed$"):
        run(adapter.convert(material))


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
