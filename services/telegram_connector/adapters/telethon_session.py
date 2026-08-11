"""Signature-gated Telethon session import adapters."""

from typing import Protocol

from .base import SessionMaterial, SessionProbeResult
from .tdata import _authorized_probe, _invalid_probe, _rejected, _user_session, _validate_envelope


class TelethonSessionConverter(Protocol):
    async def convert_telethon_file(self, data: bytes) -> bytes: ...

    async def convert_telethon_string(self, data: bytes) -> bytes: ...


class _TelethonAdapter:
    signature: bytes
    converter_method: str
    session_kind = "mtproto_user"

    def __init__(self, converter: TelethonSessionConverter, *, max_uncompressed_bytes: int = 16 * 1024 * 1024) -> None:
        if max_uncompressed_bytes < 1:
            raise ValueError("invalid import limits")
        self._converter = converter
        self._max_uncompressed_bytes = max_uncompressed_bytes

    async def probe(self, material: SessionMaterial) -> SessionProbeResult:
        try:
            self._payload(material)
        except ValueError:
            return _invalid_probe(self.name)
        return _authorized_probe(self.name)

    async def convert(self, material: SessionMaterial) -> bytes:
        payload = self._payload(material)
        try:
            converted = await getattr(self._converter, self.converter_method)(payload)
        except Exception:
            raise _rejected() from None
        return _user_session(converted)

    def _payload(self, material: SessionMaterial) -> bytes:
        if material.adapter != self.name:
            raise _rejected()
        return _validate_envelope(
            bytes(material.payload),
            signature=self.signature,
            max_uncompressed_bytes=self._max_uncompressed_bytes,
        )


class TelethonFileAdapter(_TelethonAdapter):
    name = "telethon_file"
    signature = b"TELETHON_FILE\x00"
    converter_method = "convert_telethon_file"


class TelethonStringAdapter(_TelethonAdapter):
    name = "telethon_string"
    signature = b"TELETHON_STRING\x00"
    converter_method = "convert_telethon_string"
