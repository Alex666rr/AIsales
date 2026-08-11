"""Explicit registry for the six supported authorization adapters."""

from .base import SessionAdapter


class AdapterRegistry:
    """A closed mapping: callers cannot select adapters by import path or class name."""

    _NAMES = ("phone", "qr", "tdata", "telethon_file", "telethon_string", "bot")

    def __init__(
        self,
        *,
        phone: SessionAdapter,
        qr: SessionAdapter,
        tdata: SessionAdapter,
        telethon_file: SessionAdapter,
        telethon_string: SessionAdapter,
        bot: SessionAdapter,
    ) -> None:
        values = (phone, qr, tdata, telethon_file, telethon_string, bot)
        if any(value.name != name for name, value in zip(self._NAMES, values, strict=True)):
            raise ValueError("invalid authorization adapter registry")
        self._adapters = dict(zip(self._NAMES, values, strict=True))

    @property
    def names(self) -> tuple[str, ...]:
        return self._NAMES

    def get(self, format_name: str) -> SessionAdapter:
        try:
            return self._adapters[format_name]
        except (KeyError, TypeError):
            raise ValueError("unsupported authorization adapter") from None
