"""Contract tests for session adapters and encrypted persistence."""

import asyncio
from collections.abc import Mapping
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ValidationError

from telegram_connector import (
    EncryptedSessionStore,
    QuarantinedUpload,
    SessionAdapter,
    SessionCiphertextAuthenticationError,
    SessionMaterial,
    SessionProbeResult,
    StoredSessionCiphertext,
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


class UploadEnvelope(BaseModel):
    """A caller-owned wrapper used to prove sensitive fields remain excluded when nested."""

    upload: QuarantinedUpload


class CoercibleKey:
    """A deliberately suspicious object whose __bytes__ must not authorize key input."""

    def __bytes__(self) -> bytes:
        return b"x" * 32


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


def test_sensitive_session_material_is_absent_from_public_serialization(tmp_path):
    """A payload leak through repr or Pydantic dumps must fail at every public boundary."""
    sentinel = b"RAW-SESSION-SENTINEL"
    material = SessionMaterial(
        adapter="tdata",
        payload=sentinel,
        credentials={"token": "RAW-CREDENTIAL-SENTINEL"},
    )
    original_path = tmp_path / "customer-session.tdata"
    original_path.write_bytes(sentinel)
    upload = QuarantinedUpload.capture(
        original_path=original_path,
        adapter="tdata",
        credentials={"token": "RAW-CREDENTIAL-SENTINEL"},
    )
    envelope = UploadEnvelope(upload=upload)

    public_representations = (
        repr(material),
        material.model_dump(),
        material.model_dump_json(),
        material.model_dump(include={"payload", "credentials"}),
        material.model_dump_json(include={"payload", "credentials"}),
        repr(upload),
        upload.model_dump(),
        upload.model_dump_json(),
        repr(envelope),
        envelope.model_dump(),
        envelope.model_dump_json(),
    )

    for representation in public_representations:
        assert "RAW-SESSION-SENTINEL" not in str(representation)
        assert "RAW-CREDENTIAL-SENTINEL" not in str(representation)

    assert material.model_dump() == {"adapter": "tdata"}
    assert material.model_dump(include={"payload", "credentials"}) == {}
    assert upload.model_dump() == {"upload_id": upload.upload_id, "adapter": "tdata"}


def test_store_persists_authenticated_ciphertext_with_key_version_only():
    """Replacing persisted ciphertext or retaining plaintext must break the storage contract."""
    account_id = UUID("12345678-1234-5678-1234-567812345678")
    payload = b"session bytes that must never be persisted as-is"
    store = EncryptedSessionStore.test_store({7: b"k" * 32}, active_key_version=7)

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
    reference = EncryptedSessionStore.test_store({1: b"x" * 32}, active_key_version=1).put(
        uuid4(), b"transient bytes"
    )

    assert isinstance(reference.session_id, UUID)
    assert reference.key_version == 1


@pytest.mark.parametrize(
    "key_factory",
    [
        lambda: b"k" * 32,
        lambda: bytearray(b"k" * 32),
        lambda: memoryview(bytearray(b"k" * 32)),
    ],
)
def test_store_accepts_only_explicit_bytes_like_key_material(key_factory):
    """Bytes, bytearray, and memoryview inputs are normalized without retaining mutable key state."""
    key = key_factory()
    store = EncryptedSessionStore.test_store({1: key}, active_key_version=1)
    if isinstance(key, bytearray):
        key[:] = b"z" * 32
    if isinstance(key, memoryview) and not key.readonly:
        key[:] = b"z" * 32

    reference = store.put(UUID("12345678-1234-5678-1234-567812345678"), b"session bytes")

    assert store.get(reference) == b"session bytes"
    assert "kkkk" not in repr(store)


@pytest.mark.parametrize("invalid_key", [32, "k" * 32, [107] * 32, CoercibleKey()])
def test_store_rejects_implicit_or_non_bytes_key_material_without_leaking_it(invalid_key):
    """Implicit bytes coercion must not turn arbitrary values into usable encryption keys."""
    with pytest.raises(TypeError, match="^session encryption key material must be bytes-like$") as failure:
        EncryptedSessionStore.test_store({1: invalid_key}, active_key_version=1)

    assert "kkkk" not in str(failure.value)
    assert "107" not in str(failure.value)


@pytest.mark.parametrize("ciphertext", [b"", b"n" * 11, b"n" * 12, b"n" * 27])
def test_store_rejects_malformed_ciphertext_with_a_safe_authentication_error(ciphertext):
    """Malformed records must not expose nonce, tag, key, or payload details to callers."""
    record = StoredSessionCiphertext(
        account_id=UUID("12345678-1234-5678-1234-567812345678"),
        session_id=UUID("87654321-4321-8765-4321-876543218765"),
        key_version=1,
        ciphertext=ciphertext,
    )
    store = EncryptedSessionStore.test_store({1: b"k" * 32}, active_key_version=1)

    with pytest.raises(SessionCiphertextAuthenticationError) as failure:
        store.decrypt(record)

    assert "nonce" not in str(failure.value)
    assert "tag" not in str(failure.value)
    assert "key" not in str(failure.value)


def test_store_normalizes_unknown_key_versions_to_the_safe_domain_error():
    """Records referring to retired or unavailable key versions expose no key-management detail."""
    store = EncryptedSessionStore.test_store({1: b"k" * 32}, active_key_version=1)
    reference = store.put(UUID("12345678-1234-5678-1234-567812345678"), b"session bytes")
    unknown_key_record = store.persisted_records()[0].model_copy(update={"key_version": 2})

    with pytest.raises(SessionCiphertextAuthenticationError) as failure:
        store.decrypt(unknown_key_record)

    assert str(failure.value) == "session ciphertext authentication failed"
    assert str(reference.key_version) not in str(failure.value)


def test_session_store_requires_an_explicit_repository_outside_test_construction():
    """A missing repository must never silently turn production ciphertext into process-local state."""
    with pytest.raises(TypeError):
        EncryptedSessionStore({1: b"k" * 32}, active_key_version=1)

    store = EncryptedSessionStore.test_store({1: b"k" * 32}, active_key_version=1)
    reference = store.put(UUID(int=1), b"test-only payload")

    assert store.get(reference) == b"test-only payload"
