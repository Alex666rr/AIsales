"""Tests for quarantined session-upload processing."""

import asyncio
import logging
import stat
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from telegram_connector import (
    EncryptedSessionStore,
    QuarantinedUpload,
    SessionMaterial,
    SessionQuarantineProcessor,
    UnsafeQuarantinedUpload,
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
    return QuarantinedUpload.capture(
        upload_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        original_path=original_path,
        adapter="tdata",
    )


class NeverConvertAdapter(ConvertingAdapter):
    async def convert(self, material: SessionMaterial) -> bytes:
        raise AssertionError("unsafe upload reached the adapter")


def test_successful_conversion_removes_upload_and_logs_only_safe_metadata(tmp_path, caplog):
    """A successful conversion must leave neither the source file nor sensitive log content."""
    upload = make_upload(tmp_path, "customer-export.tdata")
    processor = SessionQuarantineProcessor(
        ConvertingAdapter(),
        EncryptedSessionStore.test_store({1: b"s" * 32}, active_key_version=1),
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
        EncryptedSessionStore.test_store({1: b"s" * 32}, active_key_version=1),
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
    upload = QuarantinedUpload.capture(
        upload_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        original_path=outside_upload,
        adapter="tdata",
    )
    processor = SessionQuarantineProcessor(
        NeverConvertAdapter(),
        EncryptedSessionStore.test_store({1: b"s" * 32}, active_key_version=1),
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
    upload = QuarantinedUpload.model_construct(
        upload_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        original_path=symlink_path,
        adapter="tdata",
        credentials=(),
        expected_size=0,
        expected_sha256=b"0" * 32,
        expected_device=0,
        expected_inode=0,
    )
    processor = SessionQuarantineProcessor(
        NeverConvertAdapter(),
        EncryptedSessionStore.test_store({1: b"s" * 32}, active_key_version=1),
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
        EncryptedSessionStore.test_store({1: b"s" * 32}, active_key_version=1),
        quarantine_root=tmp_path,
    )

    reference = asyncio.run(processor.convert_and_store(UUID(int=1), upload))

    assert reference.key_version == 1
    assert not upload.original_path.exists()


def test_quarantined_upload_rejects_an_independent_material_payload(tmp_path):
    """Accepting both a path and caller-provided bytes lets conversion differ from the file being deleted."""
    original_path = tmp_path / "registered.session"
    original_path.write_bytes(b"verified upload bytes")

    with pytest.raises(ValidationError):
        QuarantinedUpload(
            original_path=original_path,
            adapter="tdata",
            material=SessionMaterial(adapter="tdata", payload=b"different bytes", credentials={}),
        )


def test_processor_reads_and_verifies_the_registered_file_before_conversion(tmp_path):
    """Replacing a registered upload before conversion must fail closed and still remove the quarantine file."""

    class RecordingAdapter:
        name = "tdata"

        def __init__(self) -> None:
            self.payloads: list[bytes] = []

        async def probe(self, material: SessionMaterial):
            raise AssertionError("probe is not used")

        async def convert(self, material: SessionMaterial) -> bytes:
            self.payloads.append(material.payload)
            return b"converted"

    original_path = tmp_path / "registered.session"
    original_path.write_bytes(b"verified upload bytes")
    assert hasattr(QuarantinedUpload, "capture"), "server-side upload capture is missing"
    upload = QuarantinedUpload.capture(original_path=original_path, adapter="tdata")
    original_path.write_bytes(b"replacement payload")
    adapter = RecordingAdapter()
    processor = SessionQuarantineProcessor(
        adapter,
        EncryptedSessionStore.test_store({1: b"s" * 32}, active_key_version=1),
        quarantine_root=tmp_path,
    )

    with pytest.raises(UnsafeQuarantinedUpload):
        asyncio.run(processor.convert_and_store(UUID(int=1), upload))

    assert adapter.payloads == []
    assert not original_path.exists()
