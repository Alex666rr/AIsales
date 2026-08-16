"""Offline conversion from parsed Desktop authorization material to Telethon sessions."""

from __future__ import annotations

from .parser import ParsedTdata


_DC_ENDPOINTS = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}


def to_telethon_string(parsed: ParsedTdata) -> str:
    """Build an in-memory Telethon session; this function performs no network calls."""
    endpoint = _DC_ENDPOINTS.get(parsed.dc_id)
    if endpoint is None or len(parsed.auth_key) != 256:
        raise ValueError("unsupported tdata authorization")
    try:
        from telethon.crypto import AuthKey
        from telethon.sessions import MemorySession, StringSession

        session = MemorySession()
        session.set_dc(parsed.dc_id, endpoint, 443)
        session.auth_key = AuthKey(parsed.auth_key)
        return StringSession.save(session)
    except Exception:
        raise ValueError("unsupported tdata authorization") from None
