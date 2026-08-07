"""Contract tests for session adapters and encrypted persistence."""

import asyncio
from collections.abc import Mapping
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from telegram_connector import (
    EncryptedSessionStore,
    SessionAdapter,
    SessionMaterial,
    SessionProbeResult,
)


class ContractAdapter:
    """A test double that declares the behavior every future adapter must provide."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def probe(self, material: SessionMaterial) -> SessionProbeResult:
        return SessionProbeResult(
            adapter=self.name,
            state="authorized",
            telegram_user_id=101,
            username="test_user",
            phone_masked=None,
            capabilities=frozenset({"send"}),
            error_code=None,
        )

    async def convert(self, material: SessionMaterial) -> bytes:
        return material.payload


@pytest.fixture
def valid_material() -> Mapping[str, SessionMaterial]:
    return {
        name: SessionMaterial(adapter=name, payload=b"test session", credentials={})
        for name in ("phone", "qr", "tdata", "telethon_file", "telethon_string", "bot")
    }


@pytest.fixture
def adapter_registry() -> Mapping[str, SessionAdapter]:
    return {
        name: ContractAdapter(name)
        for name in ("phone", "qr", "tdata", "telethon_file", "telethon_string", "bot")
    }


@pytest.mark.parametrize(
    "adapter_name",
    ["phone", "qr", "tdata", "telethon_file", "telethon_string", "bot"],
)
def test_adapter_returns_normalized_probe_result(adapter_name, adapter_registry, valid_material):
    """Every adapter reports one of the normalized, non-secret probe states."""
    result = asyncio.run(adapter_registry[adapter_name].probe(valid_material[adapter_name]))

    assert isinstance(adapter_registry[adapter_name], SessionAdapter)
    assert result.adapter == adapter_name
    assert result.state in {"authorized", "needs_code", "needs_2fa", "invalid", "unsupported"}
    assert result.capabilities == frozenset({"send"})


def test_session_value_objects_are_immutable():
    """Callers cannot mutate a material or normalized probe after validation."""
    material = SessionMaterial(adapter="phone", payload=b"test session", credentials={"region": "test"})
    result = SessionProbeResult(
        adapter="phone",
        state="needs_code",
        telegram_user_id=None,
        username=None,
        phone_masked="***0000",
        capabilities=frozenset(),
        error_code=None,
    )

    with pytest.raises(ValidationError):
        material.adapter = "bot"
    with pytest.raises(ValidationError):
        result.state = "authorized"


def test_store_persists_authenticated_ciphertext_with_key_version_only():
    """Replacing persisted ciphertext or retaining plaintext must break the storage contract."""
    account_id = UUID("12345678-1234-5678-1234-567812345678")
    payload = b"session bytes that must never be persisted as-is"
    store = EncryptedSessionStore({7: b"k" * 32}, active_key_version=7)

    reference = store.put(account_id, payload)
    persisted = store.persisted_records()[0]

    assert reference.account_id == account_id
    assert reference.key_version == 7
    assert persisted.key_version == 7
    assert persisted.ciphertext != payload
    assert "payload" not in persisted.model_dump()
    assert store.get(reference) == payload

    tampered = persisted.model_copy(
        update={"ciphertext": persisted.ciphertext[:-1] + bytes([persisted.ciphertext[-1] ^ 1])}
    )
    with pytest.raises(ValueError, match="authentication"):
        store.decrypt(tampered)


def test_store_returns_references_with_generated_ids():
    """A stored session is identified by generated metadata rather than session bytes."""
    reference = EncryptedSessionStore({1: b"x" * 32}, active_key_version=1).put(
        uuid4(), b"transient bytes"
    )

    assert isinstance(reference.session_id, UUID)
    assert reference.key_version == 1
