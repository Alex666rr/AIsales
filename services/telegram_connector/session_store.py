"""Authenticated encrypted storage boundary for converted session bytes."""

import os
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict


class SessionCiphertextAuthenticationError(ValueError):
    """Safe domain error for malformed, unauthenticated, or unavailable ciphertext."""

    def __init__(self) -> None:
        super().__init__("session ciphertext authentication failed")


class SessionRef(BaseModel):
    """Non-secret metadata identifying one encrypted session record."""

    model_config = ConfigDict(frozen=True)

    account_id: UUID
    session_id: UUID
    key_version: int


class StoredSessionCiphertext(BaseModel):
    """The complete persisted representation; it deliberately has no plaintext field."""

    model_config = ConfigDict(frozen=True)

    account_id: UUID
    session_id: UUID
    key_version: int
    ciphertext: bytes

    @property
    def reference(self) -> SessionRef:
        """Return the public non-secret identifier for this record."""
        return SessionRef(
            account_id=self.account_id,
            session_id=self.session_id,
            key_version=self.key_version,
        )


class CiphertextSessionRepository(Protocol):
    """Persistence boundary that accepts authenticated ciphertext and metadata only."""

    def save(self, record: StoredSessionCiphertext) -> None:
        """Persist an encrypted record in PostgreSQL in the concrete deployment."""

    def find(self, session_id: UUID) -> StoredSessionCiphertext | None:
        """Find a ciphertext record by its generated identifier."""

    def records(self) -> tuple[StoredSessionCiphertext, ...]:
        """Return records for contract checks without exposing any plaintext."""


class InMemoryCiphertextRepository:
    """A test-only repository stand-in; deployment supplies the PostgreSQL repository."""

    def __init__(self) -> None:
        self._records: dict[UUID, StoredSessionCiphertext] = {}

    def save(self, record: StoredSessionCiphertext) -> None:
        self._records[record.session_id] = record

    def find(self, session_id: UUID) -> StoredSessionCiphertext | None:
        return self._records.get(session_id)

    def records(self) -> tuple[StoredSessionCiphertext, ...]:
        return tuple(self._records.values())


class EncryptedSessionStore:
    """Encrypt session payloads before they cross into the persistence boundary."""

    def __init__(
        self,
        keys: Mapping[int, bytes | bytearray | memoryview],
        *,
        active_key_version: int,
        repository: CiphertextSessionRepository,
    ) -> None:
        if active_key_version not in keys:
            raise ValueError("active encryption key version is unavailable")
        self._keys = {version: self._validate_key(key) for version, key in keys.items()}
        self._active_key_version = active_key_version
        self._repository = repository

    @classmethod
    def test_store(
        cls,
        keys: Mapping[int, bytes | bytearray | memoryview],
        *,
        active_key_version: int,
    ) -> "EncryptedSessionStore":
        """Construct the explicit process-local fake used only by unit tests."""
        return cls(
            keys,
            active_key_version=active_key_version,
            repository=InMemoryCiphertextRepository(),
        )

    def put(self, account_id: UUID, payload: bytes) -> SessionRef:
        """Authenticate and encrypt transient session bytes before persistence."""
        session_id = uuid4()
        key_version = self._active_key_version
        nonce = os.urandom(12)
        ciphertext = nonce + AESGCM(self._keys[key_version]).encrypt(
            nonce,
            bytes(payload),
            self._associated_data(account_id, session_id, key_version),
        )
        record = StoredSessionCiphertext(
            account_id=account_id,
            session_id=session_id,
            key_version=key_version,
            ciphertext=ciphertext,
        )
        self._repository.save(record)
        return record.reference

    def get(self, reference: SessionRef) -> bytes:
        """Load and authenticate session bytes for the next runtime contract."""
        record = self._repository.find(reference.session_id)
        if record is None or record.reference != reference:
            raise KeyError("encrypted session reference was not found")
        return self.decrypt(record)

    def decrypt(self, record: StoredSessionCiphertext) -> bytes:
        """Decrypt one record and reject any authentication failure."""
        if len(record.ciphertext) < 28:
            raise SessionCiphertextAuthenticationError()
        nonce, encrypted_payload = record.ciphertext[:12], record.ciphertext[12:]
        try:
            return AESGCM(self._keys[record.key_version]).decrypt(
                nonce,
                encrypted_payload,
                self._associated_data(record.account_id, record.session_id, record.key_version),
            )
        except (InvalidTag, KeyError, ValueError) as error:
            raise SessionCiphertextAuthenticationError() from error

    def persisted_records(self) -> tuple[StoredSessionCiphertext, ...]:
        """Expose ciphertext-only records for persistence-boundary verification."""
        return self._repository.records()

    @staticmethod
    def _validate_key(key: object) -> bytes:
        if not isinstance(key, (bytes, bytearray, memoryview)):
            raise TypeError("session encryption key material must be bytes-like")
        key_bytes = bytes(key)
        if len(key_bytes) not in (16, 24, 32):
            raise ValueError("AES-GCM keys must be 128, 192, or 256 bits")
        return key_bytes

    @staticmethod
    def _associated_data(account_id: UUID, session_id: UUID, key_version: int) -> bytes:
        return f"{account_id}:{session_id}:{key_version}".encode("ascii")
