"""Quarantined upload processing with guaranteed original-file cleanup."""

import hashlib
import logging
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .adapters.base import SessionAdapter, SessionMaterial
from .session_store import EncryptedSessionStore, SessionRef


logger = logging.getLogger(__name__)


class QuarantinedUpload(BaseModel):
    """A transient local upload whose original file must be removed after conversion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    upload_id: UUID = Field(default_factory=uuid4)
    original_path: Path = Field(exclude=True, repr=False)
    adapter: str
    credentials: tuple[tuple[str, SecretStr], ...] = Field(default_factory=tuple, exclude=True, repr=False)
    expected_size: int = Field(ge=0, exclude=True, repr=False)
    expected_sha256: bytes = Field(min_length=32, max_length=32, exclude=True, repr=False)
    expected_device: int = Field(exclude=True, repr=False)
    expected_inode: int = Field(exclude=True, repr=False)

    @classmethod
    def capture(
        cls,
        *,
        original_path: Path,
        adapter: str,
        credentials: Mapping[str, SecretStr | str] | None = None,
        upload_id: UUID | None = None,
        max_bytes: int = 16 * 1024 * 1024,
    ) -> "QuarantinedUpload":
        """Capture server-owned file identity and digest without retaining its payload."""
        payload, file_stat = _read_regular_file(Path(original_path), max_bytes=max_bytes)
        material = SessionMaterial(adapter=adapter, payload=b"", credentials=credentials or {})
        return cls(
            upload_id=upload_id or uuid4(),
            original_path=original_path,
            adapter=adapter,
            credentials=material.credentials,
            expected_size=len(payload),
            expected_sha256=hashlib.sha256(payload).digest(),
            expected_device=file_stat.st_dev,
            expected_inode=file_stat.st_ino,
        )


class UnsafeQuarantinedUpload(ValueError):
    """Safe domain error for an upload path that cannot be quarantined."""

    def __init__(self) -> None:
        super().__init__("unsafe quarantined upload")


class SessionQuarantineProcessor:
    """Convert one quarantined upload and persist only its encrypted session bytes."""

    def __init__(
        self,
        adapter: SessionAdapter,
        session_store: EncryptedSessionStore,
        *,
        quarantine_root: Path,
        max_upload_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        if max_upload_bytes < 1:
            raise ValueError("invalid quarantine upload limit")
        self._adapter = adapter
        self._session_store = session_store
        resolved_root = Path(quarantine_root).resolve(strict=True)
        if not resolved_root.is_dir():
            raise ValueError("quarantine root must be a directory")
        self._quarantine_root = resolved_root
        self._max_upload_bytes = max_upload_bytes

    async def convert_and_store(self, account_id: UUID, upload: QuarantinedUpload) -> SessionRef:
        """Convert the upload, record a safe status, and delete the original in all cases."""
        try:
            original_path = self._validated_original_path(upload.original_path)
        except UnsafeQuarantinedUpload:
            logger.info("session_upload upload_id=%s status=rejected", upload.upload_id)
            raise
        try:
            payload = self._verified_payload(original_path, upload)
            material = SessionMaterial(
                adapter=upload.adapter,
                payload=payload,
                credentials=upload.credentials,
            )
            converted_payload = await self._adapter.convert(material)
            reference = self._session_store.put(account_id, converted_payload)
        except UnsafeQuarantinedUpload:
            logger.info("session_upload upload_id=%s status=rejected", upload.upload_id)
            raise
        except Exception:
            logger.info("session_upload upload_id=%s status=conversion_failed", upload.upload_id)
            raise
        else:
            logger.info("session_upload upload_id=%s status=stored", upload.upload_id)
            return reference
        finally:
            original_path.unlink(missing_ok=True)

    def _verified_payload(self, original_path: Path, upload: QuarantinedUpload) -> bytes:
        if upload.adapter != self._adapter.name:
            raise UnsafeQuarantinedUpload()
        payload, file_stat = _read_regular_file(original_path, max_bytes=self._max_upload_bytes)
        if (
            len(payload) != upload.expected_size
            or hashlib.sha256(payload).digest() != upload.expected_sha256
            or file_stat.st_dev != upload.expected_device
            or file_stat.st_ino != upload.expected_inode
        ):
            raise UnsafeQuarantinedUpload()
        return payload

    def _validated_original_path(self, original_path: Path) -> Path:
        """Accept only a non-reparse regular file physically contained by quarantine."""
        try:
            if original_path.is_symlink() or self._is_reparse_point(original_path):
                raise UnsafeQuarantinedUpload()
            resolved_path = original_path.resolve(strict=True)
            if not resolved_path.is_relative_to(self._quarantine_root) or not resolved_path.is_file():
                raise UnsafeQuarantinedUpload()
            return resolved_path
        except (OSError, RuntimeError, ValueError) as error:
            if isinstance(error, UnsafeQuarantinedUpload):
                raise
            raise UnsafeQuarantinedUpload() from error

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        """Detect Windows reparse points without following them."""
        file_attributes = getattr(path.lstat(), "st_file_attributes", 0)
        reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return bool(file_attributes & reparse_point)


def _read_regular_file(path: Path, *, max_bytes: int) -> tuple[bytes, os.stat_result]:
    """Open one file without following links and verify the opened identity."""
    if max_bytes < 1:
        raise UnsafeQuarantinedUpload()
    descriptor: int | None = None
    try:
        path_stat = path.lstat()
        reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if stat.S_ISLNK(path_stat.st_mode) or bool(getattr(path_stat, "st_file_attributes", 0) & reparse_point):
            raise UnsafeQuarantinedUpload()
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or opened_stat.st_dev != path_stat.st_dev
            or opened_stat.st_ino != path_stat.st_ino
            or opened_stat.st_size > max_bytes
        ):
            raise UnsafeQuarantinedUpload()
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_bytes or len(payload) != opened_stat.st_size:
            raise UnsafeQuarantinedUpload()
        return payload, opened_stat
    except (OSError, RuntimeError, ValueError) as error:
        if isinstance(error, UnsafeQuarantinedUpload):
            raise
        raise UnsafeQuarantinedUpload() from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
