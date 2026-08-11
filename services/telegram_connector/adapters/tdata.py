"""Safe, signature-gated importer for the prototype TData format."""

import io
import gzip
import posixpath
import stat
import tarfile
import zipfile
from typing import Protocol

from .base import SessionMaterial, SessionProbeResult


class TDataConverter(Protocol):
    async def convert_tdata(self, data: bytes) -> bytes: ...


_TDATA_SIGNATURE = b"TDATA\x00"
_SCHEMA_VERSION = 1
_SAFE_IMPORT_ERROR = "unsupported session import"
_ZIP_COMPRESSION_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})


class _ArchiveLimitExceeded(Exception):
    """Internal sentinel: never expose stream or archive implementation details."""


class _BoundedReader:
    """Hard aggregate cap around every byte tarfile can receive after decompression."""

    def __init__(self, source: object, limit: int) -> None:
        self._source = source
        self._remaining = limit
        self._position = 0

    def read(self, size: int = -1) -> bytes:
        self._claim(size)
        result = self._source.read(size)  # type: ignore[attr-defined]
        self._record(result)
        return result

    def readinto(self, buffer: bytearray | memoryview) -> int:
        self._claim(len(buffer))
        reader = getattr(self._source, "readinto", None)
        if reader is None:
            result = self.read(len(buffer))
            buffer[: len(result)] = result
            return len(result)
        count = reader(buffer)
        if count is None:
            return 0
        self._record_count(count)
        return count

    def read1(self, size: int = -1) -> bytes:
        return self.read(size)

    def readinto1(self, buffer: bytearray | memoryview) -> int:
        return self.readinto(buffer)

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        # Streaming tar parsing must not seek around the cap and re-read decompressed bytes.
        raise _ArchiveLimitExceeded()

    def tell(self) -> int:
        return self._position

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def _claim(self, size: int) -> None:
        if size is None or size < 0 or size > self._remaining:
            raise _ArchiveLimitExceeded()

    def _record(self, result: bytes) -> None:
        self._record_count(len(result))

    def _record_count(self, count: int) -> None:
        if count < 0 or count > self._remaining:
            raise _ArchiveLimitExceeded()
        self._remaining -= count
        self._position += count


def _rejected() -> ValueError:
    return ValueError(_SAFE_IMPORT_ERROR)


def _normalized_member_name(name: str) -> tuple[str, str]:
    """Return canonical path and collision key, rejecting archive traversal syntax."""
    raw = name.replace("\\", "/")
    if not raw or raw.startswith("/") or ":" in raw:
        raise _rejected()
    normalized = posixpath.normpath(raw)
    if normalized in {".", ".."} or normalized.startswith("../"):
        raise _rejected()
    canonical = normalized + "/" if raw.endswith("/") else normalized
    return canonical, normalized


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
                seen: set[str] = set()
                session_member = None
                for member in members:
                    canonical, collision_key = _normalized_member_name(member.filename)
                    if collision_key in seen:
                        raise _rejected()
                    seen.add(collision_key)
                    if member.flag_bits & 0x1 or member.compress_type not in _ZIP_COMPRESSION_METHODS:
                        raise _rejected()
                    if member.is_dir():
                        entry_type = stat.S_IFMT(member.external_attr >> 16)
                        if entry_type not in (0, stat.S_IFDIR):
                            raise _rejected()
                        continue
                    mode = member.external_attr >> 16
                    entry_type = stat.S_IFMT(mode)
                    if entry_type not in (0, stat.S_IFREG):
                        raise _rejected()
                    total += member.file_size
                    if total > max_uncompressed_bytes:
                        raise _rejected()
                    if canonical != "tdata/session.bin" or session_member is not None:
                        raise _rejected()
                    session_member = member
                if session_member is None:
                    raise _rejected()
                with archive.open(session_member) as source:
                    body = source.read(max_uncompressed_bytes + 1)
                if len(body) > max_uncompressed_bytes:
                    raise _rejected()
                return body
        if data.startswith(b"\x1f\x8b"):
            decompressed = gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb")
        elif len(data) >= 512 and data[257:262] == b"ustar":
            decompressed = io.BytesIO(data)
        else:
            raise _rejected()
        # Streaming mode plus the outer limiter caps PAX/GNU metadata before tarfile parses it.
        with decompressed:
            with tarfile.open(
                fileobj=_BoundedReader(decompressed, max_uncompressed_bytes), mode="r|"
            ) as archive:
                total = 0
                count = 0
                seen: set[str] = set()
                body: bytes | None = None
                for member in archive:
                    count += 1
                    if count > 16:
                        raise _rejected()
                    canonical, collision_key = _normalized_member_name(member.name)
                    if collision_key in seen:
                        raise _rejected()
                    seen.add(collision_key)
                    if member.isdir():
                        continue
                    if not member.isreg():
                        raise _rejected()
                    total += member.size
                    if total > max_uncompressed_bytes:
                        raise _rejected()
                    if canonical != "tdata/session.bin" or body is not None:
                        raise _rejected()
                    source = archive.extractfile(member)
                    if source is None:
                        raise _rejected()
                    with source:
                        body = source.read(max_uncompressed_bytes + 1)
                    if len(body) > max_uncompressed_bytes or len(body) != member.size:
                        raise _rejected()
                if body is None:
                    raise _rejected()
                return body
    except (
        _ArchiveLimitExceeded,
        OSError,
        EOFError,
        RuntimeError,
        NotImplementedError,
        ValueError,
        tarfile.TarError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ):
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
