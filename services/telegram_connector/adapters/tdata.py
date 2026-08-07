"""Safe, signature-gated importer for the prototype TData format."""

import io
import stat
import tarfile
import zipfile
from collections.abc import Awaitable, Callable
from typing import Protocol

from .base import SessionMaterial, SessionProbeResult


class TDataConverter(Protocol):
    async def convert_tdata(self, data: bytes) -> bytes: ...


_TDATA_SIGNATURE = b"TDATA\x00"
_SCHEMA_VERSION = 1
_SAFE_IMPORT_ERROR = "unsupported session import"


def _rejected() -> ValueError:
    return ValueError(_SAFE_IMPORT_ERROR)


def _safe_member_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    return bool(normalized) and not normalized.startswith("/") and ":" not in normalized and ".." not in normalized.split("/")


def _validate_envelope(data: bytes, *, signature: bytes, max_uncompressed_bytes: int) -> bytes:
    if not data.startswith(signature) or len(data) <= len(signature):
        raise _rejected()
    if data[len(signature)] != _SCHEMA_VERSION:
        raise _rejected()
    body = data[len(signature) + 1 :]
    if not body or len(body) > max_uncompressed_bytes:
        raise _rejected()
    return body


def _archive_member_payload(data: bytes, *, max_compressed_bytes: int, max_uncompressed_bytes: int) -> bytes:
    if len(data) > max_compressed_bytes:
        raise _rejected()
    try:
        if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                if not members or len(members) > 16:
                    raise _rejected()
                total = 0
                files = []
                for member in members:
                    if not _safe_member_name(member.filename) or stat.S_ISLNK(member.external_attr >> 16):
                        raise _rejected()
                    if member.is_dir():
                        continue
                    total += member.file_size
                    if total > max_uncompressed_bytes:
                        raise _rejected()
                    files.append(member)
                if len(files) != 1 or files[0].filename != "tdata/session.bin":
                    raise _rejected()
                with archive.open(files[0]) as source:
                    body = source.read(max_uncompressed_bytes + 1)
                if len(body) > max_uncompressed_bytes:
                    raise _rejected()
                return body
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            members = archive.getmembers()
            if not members or len(members) > 16:
                raise _rejected()
            total = 0
            files = []
            for member in members:
                if not _safe_member_name(member.name) or not member.isreg():
                    raise _rejected()
                total += member.size
                if total > max_uncompressed_bytes:
                    raise _rejected()
                files.append(member)
            if len(files) != 1 or files[0].name != "tdata/session.bin":
                raise _rejected()
            source = archive.extractfile(files[0])
            if source is None:
                raise _rejected()
            with source:
                body = source.read(max_uncompressed_bytes + 1)
            if len(body) > max_uncompressed_bytes:
                raise _rejected()
            return body
    except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile, zipfile.LargeZipFile):
        raise _rejected() from None


class TDataAdapter:
    """Converts a validated TData envelope through an injected, offline-testable converter."""

    name = "tdata"
    session_kind = "mtproto_user"

    def __init__(
        self,
        converter: TDataConverter,
        *,
        max_compressed_bytes: int = 4 * 1024 * 1024,
        max_uncompressed_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        if max_compressed_bytes < 1 or max_uncompressed_bytes < 1:
            raise ValueError("invalid import limits")
        self._converter = converter
        self._max_compressed_bytes = max_compressed_bytes
        self._max_uncompressed_bytes = max_uncompressed_bytes

    async def probe(self, material: SessionMaterial) -> SessionProbeResult:
        try:
            self._validated_payload(material)
        except ValueError:
            return _invalid_probe(self.name)
        return _authorized_probe(self.name)

    async def convert(self, material: SessionMaterial) -> bytes:
        payload = self._validated_payload(material)
        try:
            converted = await self._converter.convert_tdata(payload)
        except Exception:
            raise _rejected() from None
        return _user_session(converted)

    def _validated_payload(self, material: SessionMaterial) -> bytes:
        if material.adapter != self.name:
            raise _rejected()
        data = bytes(material.payload)
        if data.startswith(_TDATA_SIGNATURE):
            return _validate_envelope(
                data,
                signature=_TDATA_SIGNATURE,
                max_uncompressed_bytes=self._max_uncompressed_bytes,
            )
        archive_payload = _archive_member_payload(
            data,
            max_compressed_bytes=self._max_compressed_bytes,
            max_uncompressed_bytes=self._max_uncompressed_bytes,
        )
        return _validate_envelope(
            archive_payload,
            signature=_TDATA_SIGNATURE,
            max_uncompressed_bytes=self._max_uncompressed_bytes,
        )


def _user_session(value: object) -> bytes:
    if not isinstance(value, bytes) or not value or value.startswith(b"BOT_API_SESSION\x00"):
        raise _rejected()
    return value


def _authorized_probe(name: str) -> SessionProbeResult:
    return SessionProbeResult(
        adapter=name,
        state="authorized",
        telegram_user_id=None,
        username=None,
        phone_masked=None,
        capabilities=frozenset({"mtproto_user"}),
        error_code=None,
    )


def _invalid_probe(name: str) -> SessionProbeResult:
    return SessionProbeResult(
        adapter=name,
        state="invalid",
        telegram_user_id=None,
        username=None,
        phone_masked=None,
        capabilities=frozenset(),
        error_code="invalid_session_import",
    )
