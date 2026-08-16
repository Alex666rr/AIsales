from telethon.sessions import StringSession

from telegram_connector.importers.tdata.parser import ParsedTdata
from telegram_connector.importers.tdata.telethon import to_telethon_string


def test_converter_creates_a_telethon_string_session_from_parsed_tdata() -> None:
    session_value = to_telethon_string(
        ParsedTdata(local_key=b"l" * 256, user_id=123456, dc_id=2, auth_key=b"a" * 256)
    )

    restored = StringSession(session_value)

    assert restored.dc_id == 2
    assert restored.server_address == "149.154.167.51"
    assert restored.port == 443
    assert restored.auth_key.key == b"a" * 256
