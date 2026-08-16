import hashlib
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from telegram_connector.importers.tdata.parser import (
    TdataPasscodeRejected,
    TdataParseRejected,
    _account_file_name,
    parse_tdata,
)
from telegram_connector.importers.tdata.preflight import prepare_tdata_copy


def _derive_key(salt: bytes, passcode: str) -> bytes:
    material = hashlib.sha512(salt + passcode.encode("utf-8") + salt).digest()
    return hashlib.pbkdf2_hmac("sha512", material, salt, 100_000 if passcode else 1, 256)


def _aes_key_and_iv(auth_key: bytes, message_key: bytes) -> tuple[bytes, bytes]:
    offset = 8
    sha1_a = hashlib.sha1(message_key + auth_key[offset : offset + 32]).digest()
    sha1_b = hashlib.sha1(auth_key[offset + 32 : offset + 48] + message_key + auth_key[offset + 48 : offset + 64]).digest()
    sha1_c = hashlib.sha1(auth_key[offset + 64 : offset + 96] + message_key).digest()
    sha1_d = hashlib.sha1(message_key + auth_key[offset + 96 : offset + 128]).digest()
    return sha1_a[:8] + sha1_b[8:20] + sha1_c[4:16], sha1_a[8:20] + sha1_b[:8] + sha1_c[16:20] + sha1_d[:8]


def _ige_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    previous_ciphertext, previous_plaintext = iv[:16], iv[16:]
    ciphertext = bytearray()
    for offset in range(0, len(plaintext), 16):
        block = plaintext[offset : offset + 16]
        encrypted = encryptor.update(bytes(left ^ right for left, right in zip(block, previous_ciphertext)))
        current = bytes(left ^ right for left, right in zip(encrypted, previous_plaintext))
        ciphertext.extend(current)
        previous_ciphertext, previous_plaintext = current, block
    return bytes(ciphertext)


def _encrypt_descriptor(payload: bytes, auth_key: bytes) -> bytes:
    plaintext = (len(payload) + 4).to_bytes(4, "little") + payload
    plaintext += b"\x00" * (-len(plaintext) % 16)
    message_key = hashlib.sha1(plaintext).digest()[:16]
    aes_key, aes_iv = _aes_key_and_iv(auth_key, message_key)
    return message_key + _ige_encrypt(plaintext, aes_key, aes_iv)


def _qbytearrays(*values: bytes) -> bytes:
    return b"".join(len(value).to_bytes(4, "big") + value for value in values)


def _tdf(payload: bytes, *, version: int = 70009) -> bytes:
    magic = b"TDF$"
    checksum_input = payload + len(payload).to_bytes(4, "little") + version.to_bytes(4, "little") + magic
    return magic + version.to_bytes(4, "little") + payload + hashlib.md5(checksum_input).digest()


def _fixture_account_file_name(index: int) -> str:
    data_name = "data" if index == 0 else f"data#{index + 1}"
    digest = hashlib.md5(data_name.encode("utf-8")).digest()
    return "".join(f"{byte & 0x0f:X}{byte >> 4:X}" for byte in digest[:8])


def test_parser_uses_tdesktop_64_bit_account_file_names() -> None:
    assert _account_file_name(0) == "D877F783D5D3EF8C"


def _snapshot_with_key_data(
    tmp_path: Path, *, passcode: str = "desktop-passcode", wide_user_id: bool = False
):
    salt = b"s" * 32
    local_key = b"l" * 256
    passcode_key = _derive_key(salt, passcode)
    encrypted_local_key = _encrypt_descriptor(local_key, passcode_key)
    account_auth_key = b"a" * 256
    info = (1).to_bytes(4, "big", signed=True) + (0).to_bytes(4, "big", signed=True) + (0).to_bytes(4, "big", signed=True)
    user_header = (
        (-1).to_bytes(8, "big", signed=True)
        + (123456).to_bytes(8, "big", signed=True)
        + (2).to_bytes(4, "big", signed=True)
        if wide_user_id
        else (123456).to_bytes(4, "big", signed=True) + (2).to_bytes(4, "big", signed=True)
    )
    authorization = (
        user_header
        + (1).to_bytes(4, "big", signed=True)
        + (2).to_bytes(4, "big", signed=True)
        + account_auth_key
        + (0).to_bytes(4, "big", signed=True)
    )
    account_payload = (75).to_bytes(4, "big", signed=True) + _qbytearrays(authorization)
    source = tmp_path / "source" / "tdata"
    source.mkdir(parents=True)
    (source / "key_datas").write_bytes(
        _tdf(_qbytearrays(salt, encrypted_local_key, _encrypt_descriptor(info, local_key)))
    )
    (source / f"{_fixture_account_file_name(0)}s").write_bytes(_tdf(_qbytearrays(_encrypt_descriptor(account_payload, local_key))))
    return prepare_tdata_copy(source, tmp_path / "private-copy", max_bytes=1_000_000), local_key, account_auth_key


def test_parser_reads_the_local_key_from_a_valid_key_datas_file(tmp_path: Path) -> None:
    snapshot, local_key, account_auth_key = _snapshot_with_key_data(tmp_path)

    parsed = parse_tdata(snapshot, passcode="desktop-passcode")

    assert parsed.local_key == local_key
    assert parsed.user_id == 123456
    assert parsed.dc_id == 2
    assert parsed.auth_key == account_auth_key
    assert local_key.hex() not in repr(parsed)
    assert account_auth_key.hex() not in repr(parsed)


def test_parser_reads_tdesktop_wide_account_identifiers(tmp_path: Path) -> None:
    snapshot, _, _ = _snapshot_with_key_data(tmp_path, wide_user_id=True)

    parsed = parse_tdata(snapshot, passcode="desktop-passcode")

    assert parsed.user_id == 123456
    assert parsed.dc_id == 2


def test_parser_uses_the_first_account_when_tdesktop_has_no_active_index(tmp_path: Path) -> None:
    snapshot, _, _ = _snapshot_with_key_data(tmp_path)
    key_data = snapshot.root / "key_datas"
    original = key_data.read_bytes()
    assert original

    # The fixture has a selected index of 0. Rebuild only its valid envelope
    # with Telegram Desktop's -1 sentinel, meaning "no selected account".
    source = tmp_path / "sentinel-source" / "tdata"
    source.mkdir(parents=True)
    salt = b"s" * 32
    local_key = b"l" * 256
    passcode_key = _derive_key(salt, "desktop-passcode")
    info = (
        (1).to_bytes(4, "big", signed=True)
        + (0).to_bytes(4, "big", signed=True)
        + (-1).to_bytes(4, "big", signed=True)
    )
    account_auth_key = b"a" * 256
    authorization = (
        (123456).to_bytes(4, "big", signed=True)
        + (2).to_bytes(4, "big", signed=True)
        + (1).to_bytes(4, "big", signed=True)
        + (2).to_bytes(4, "big", signed=True)
        + account_auth_key
        + (0).to_bytes(4, "big", signed=True)
    )
    account_payload = (75).to_bytes(4, "big", signed=True) + _qbytearrays(authorization)
    (source / "key_datas").write_bytes(
        _tdf(
            _qbytearrays(
                salt,
                _encrypt_descriptor(local_key, passcode_key),
                _encrypt_descriptor(info, local_key),
            )
        )
    )
    (source / f"{_fixture_account_file_name(0)}s").write_bytes(
        _tdf(_qbytearrays(_encrypt_descriptor(account_payload, local_key)))
    )
    sentinel_snapshot = prepare_tdata_copy(source, tmp_path / "sentinel-copy", max_bytes=1_000_000)

    parsed = parse_tdata(sentinel_snapshot, passcode="desktop-passcode")

    assert parsed.user_id == 123456


def test_parser_rejects_a_wrong_desktop_passcode_without_leaking_it(tmp_path: Path) -> None:
    snapshot, _, _ = _snapshot_with_key_data(tmp_path)

    with pytest.raises(TdataPasscodeRejected, match="^tdata passcode rejected$") as error:
        parse_tdata(snapshot, passcode="wrong-passcode")

    assert "wrong-passcode" not in str(error.value)


def test_parser_rejects_an_invalid_key_datas_container(tmp_path: Path) -> None:
    source = tmp_path / "source" / "tdata"
    source.mkdir(parents=True)
    (source / "key_datas").write_bytes(b"not-a-tdata-file")
    snapshot = prepare_tdata_copy(source, tmp_path / "private-copy", max_bytes=1_000_000)

    with pytest.raises(TdataParseRejected, match="^unsupported tdata data$"):
        parse_tdata(snapshot, passcode=None)
