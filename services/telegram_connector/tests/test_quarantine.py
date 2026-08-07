"""Tests for quarantined session-upload processing."""

import asyncio
import logging
import stat
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


class NeverConvertAdapter(ConvertingAdapter):
    async def convert(self, material: SessionMaterial) -> bytes:
        raise AssertionError("unsafe upload reached the adapter")


def test_successful_conversion_removes_upload_and_logs_only_safe_metadata(tmp_path, caplog):
    """A successful conversion must leave neither the source file nor sensitive log content."""
    upload = make_upload(tmp_path, "customer-export.tdata")
    processor = SessionQuarantineProcessor(
        ConvertingAdapter(),
        EncryptedSessionStore({1: b"s" * 32}, active_key_version=1),
        quarantine_root=tmp_path,
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
        FailingAdapter(),
        EncryptedSessionStore({1: b"s" * 32}, active_key_version=1),
        quarantine_root=tmp_path,
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


def test_outside_quarantine_root_is_refused_without_deleting_external_file(tmp_path, caplog):
    """A caller-controlled path outside the configured root must never reach cleanup or conversion."""
    quarantine_root = tmp_path / "quarantine"
    quarantine_root.mkdir()
    outside_upload = tmp_path / "external-session.tdata"
    outside_upload.write_bytes(b"external sensitive upload")
    upload = QuarantinedUpload(
        upload_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        original_path=outside_upload,
        material=SessionMaterial(adapter="tdata", payload=b"external sensitive upload", credentials={}),
    )
    processor = SessionQuarantineProcessor(
        NeverConvertAdapter(),
        EncryptedSessionStore({1: b"s" * 32}, active_key_version=1),
        quarantine_root=quarantine_root,
    )

    with caplog.at_level(logging.INFO, logger="telegram_connector.quarantine"):
        with pytest.raises(ValueError, match="unsafe quarantined upload"):
            asyncio.run(processor.convert_and_store(UUID(int=1), upload))

    assert outside_upload.exists()
    assert caplog.messages == ["session_upload upload_id=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa status=rejected"]
    assert "external-session.tdata" not in caplog.text
    assert "external sensitive upload" not in caplog.text


def test_symlinked_quarantine_upload_is_refused_without_deleting_target(tmp_path, caplog):
    """A symlink inside quarantine must not become a delete-capable alias for an external file."""
    quarantine_root = tmp_path / "quarantine"
    quarantine_root.mkdir()
    external_target = tmp_path / "external-session.tdata"
    external_target.write_bytes(b"external sensitive upload")
    symlink_path = quarantine_root / "linked-session.tdata"
    try:
        symlink_path.symlink_to(external_target)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable on this platform: {error}")
    upload = QuarantinedUpload(
        upload_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        original_path=symlink_path,
        material=SessionMaterial(adapter="tdata", payload=b"external sensitive upload", credentials={}),
    )
    processor = SessionQuarantineProcessor(
        NeverConvertAdapter(),
        EncryptedSessionStore({1: b"s" * 32}, active_key_version=1),
        quarantine_root=quarantine_root,
    )

    with caplog.at_level(logging.INFO, logger="telegram_connector.quarantine"):
        with pytest.raises(ValueError, match="unsafe quarantined upload"):
            asyncio.run(processor.convert_and_store(UUID(int=1), upload))

    assert external_target.exists()
    assert symlink_path.exists()
    assert caplog.messages == ["session_upload upload_id=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa status=rejected"]
    assert "linked-session.tdata" not in caplog.text
    assert "external sensitive upload" not in caplog.text


def test_regular_upload_is_accepted_when_reparse_attributes_are_unavailable(tmp_path, monkeypatch):
    """Platforms without Windows reparse metadata must still accept regular quarantine files."""
    monkeypatch.delattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", raising=False)
    upload = make_upload(tmp_path, "customer-export.tdata")
    processor = SessionQuarantineProcessor(
        ConvertingAdapter(),
        EncryptedSessionStore({1: b"s" * 32}, active_key_version=1),
        quarantine_root=tmp_path,
    )

    reference = asyncio.run(processor.convert_and_store(UUID(int=1), upload))

    assert reference.key_version == 1
    assert not upload.original_path.exists()
