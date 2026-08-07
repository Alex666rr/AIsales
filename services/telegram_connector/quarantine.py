"""Quarantined upload processing with guaranteed original-file cleanup."""

import logging
import stat
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .adapters.base import SessionAdapter, SessionMaterial
from .session_store import EncryptedSessionStore, SessionRef


logger = logging.getLogger(__name__)


class QuarantinedUpload(BaseModel):
    """A transient local upload whose original file must be removed after conversion."""

    model_config = ConfigDict(frozen=True)

    upload_id: UUID = Field(default_factory=uuid4)
    original_path: Path = Field(exclude=True, repr=False)
    material: SessionMaterial = Field(exclude=True, repr=False)


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
    ) -> None:
        self._adapter = adapter
        self._session_store = session_store
        resolved_root = Path(quarantine_root).resolve(strict=True)
        if not resolved_root.is_dir():
            raise ValueError("quarantine root must be a directory")
        self._quarantine_root = resolved_root

    async def convert_and_store(self, account_id: UUID, upload: QuarantinedUpload) -> SessionRef:
        """Convert the upload, record a safe status, and delete the original in all cases."""
        try:
            original_path = self._validated_original_path(upload.original_path)
        except UnsafeQuarantinedUpload:
            logger.info("session_upload upload_id=%s status=rejected", upload.upload_id)
            raise
        try:
            converted_payload = await self._adapter.convert(upload.material)
            reference = self._session_store.put(account_id, converted_payload)
        except Exception:
            logger.info("session_upload upload_id=%s status=conversion_failed", upload.upload_id)
            raise
        else:
            logger.info("session_upload upload_id=%s status=stored", upload.upload_id)
            return reference
        finally:
            original_path.unlink(missing_ok=True)

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
