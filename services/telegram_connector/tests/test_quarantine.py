"""Tests for quarantined session-upload processing."""

import asyncio
import logging
from pathlib import Path
from uuid import UUID

import pytest

from telegram_connector import (
    EncryptedSessionStore,
    QuarantinedUpload,
    SessionMaterial,
    SessionQuarantineProcessor,
)


class ConvertingAdapter:
    name = "tdata"

    async def probe(self, material: SessionMaterial):
        raise AssertionError("probe is not part of upload conversion")

    async def convert(self, material: SessionMaterial) -> bytes:
        return b"converted session"


class FailingAdapter(ConvertingAdapter):
    async def convert(self, material: SessionMaterial) -> bytes:
        raise ValueError("conversion rejected")


def make_upload(tmp_path: Path, original_name: str) -> QuarantinedUpload:
    original_path = tmp_path / original_name
    original_path.write_bytes(b"highly sensitive uploaded session")
    return QuarantinedUpload(
        upload_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        original_path=original_path,
        material=SessionMaterial(adapter="tdata", payload=original_path.read_bytes(), credentials={}),
    )


def test_successful_conversion_removes_upload_and_logs_only_safe_metadata(tmp_path, caplog):
    """A successful conversion must leave neither the source file nor sensitive log content."""
    upload = make_upload(tmp_path, "customer-export.tdata")
    processor = SessionQuarantineProcessor(
        ConvertingAdapter(), EncryptedSessionStore({1: b"s" * 32}, active_key_version=1)
    )

    with caplog.at_level(logging.INFO, logger="telegram_connector.quarantine"):
        reference = asyncio.run(processor.convert_and_store(UUID(int=1), upload))

    assert reference.key_version == 1
    assert not upload.original_path.exists()
    assert caplog.messages == ["session_upload upload_id=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa status=stored"]
    assert "customer-export.tdata" not in caplog.text
    assert "highly sensitive uploaded session" not in caplog.text


def test_failed_conversion_still_removes_upload_and_logs_only_safe_metadata(tmp_path, caplog):
    """A conversion failure must delete the source file in the finally path without leaking details."""
    upload = make_upload(tmp_path, "customer-export.tdata")
    processor = SessionQuarantineProcessor(
        FailingAdapter(), EncryptedSessionStore({1: b"s" * 32}, active_key_version=1)
    )

    with caplog.at_level(logging.INFO, logger="telegram_connector.quarantine"):
        with pytest.raises(ValueError, match="conversion rejected"):
            asyncio.run(processor.convert_and_store(UUID(int=1), upload))

    assert not upload.original_path.exists()
    assert caplog.messages == [
        "session_upload upload_id=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa status=conversion_failed"
    ]
    assert "customer-export.tdata" not in caplog.text
    assert "highly sensitive uploaded session" not in caplog.text
