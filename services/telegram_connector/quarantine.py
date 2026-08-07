"""Quarantined upload processing with guaranteed original-file cleanup."""

import logging
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
    original_path: Path = Field(repr=False)
    material: SessionMaterial = Field(repr=False)


class SessionQuarantineProcessor:
    """Convert one quarantined upload and persist only its encrypted session bytes."""

    def __init__(self, adapter: SessionAdapter, session_store: EncryptedSessionStore) -> None:
        self._adapter = adapter
        self._session_store = session_store

    async def convert_and_store(self, account_id: UUID, upload: QuarantinedUpload) -> SessionRef:
        """Convert the upload, record a safe status, and delete the original in all cases."""
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
            upload.original_path.unlink(missing_ok=True)
