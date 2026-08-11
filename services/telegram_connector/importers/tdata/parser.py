"""Offline parser for the Telegram Desktop ``key_datas`` local-key envelope."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .preflight import TdataSnapshot


_TDF_MAGIC = b"TDF$"
_LOCAL_KEY_BYTES = 256
_AES_BLOCK_BYTES = 16


class TdataParseRejected(ValueError):
    """Safe error for malformed or unsupported Desktop tdata data."""

    def __init__(self) -> None:
        super().__init__("unsupported tdata data")


class TdataPasscodeRejected(ValueError):
    """Safe error for a missing or incorrect Telegram Desktop local passcode."""

    def __init__(self) -> None:
        super().__init__("tdata passcode rejected")


@dataclass(frozen=True, slots=True)
class ParsedTdata:
    """Single-purpose local-key material; account authorization is parsed separately."""

    local_key: bytes = field(repr=False)
    user_id: int
    dc_id: int
    auth_key: bytes = field(repr=False)


def parse_tdata(snapshot: TdataSnapshot, *, passcode: str | None) -> ParsedTdata:
    """Read and decrypt the local key from a guarded private tdata snapshot."""
    try:
        snapshot.assert_source_unchanged()
        payload, _ = _read_tdf(snapshot.root / "key_datas")
        salt, encrypted_local_key, encrypted_info = _read_qbytearrays(payload, count=3)
        if not salt or not encrypted_local_key:
            raise TdataParseRejected()
        passcode_key = _derive_local_key(salt, passcode or "")
        local_key = _decrypt_descriptor(encrypted_local_key, passcode_key)
        if len(local_key) != _LOCAL_KEY_BYTES:
            raise TdataParseRejected()
        account_index = _read_active_account_index(_decrypt_descriptor(encrypted_info, local_key))
        account_payload, _ = _read_tdf_with_suffixes(snapshot.root, _account_file_name(account_index))
        (encrypted_authorization,) = _read_qbytearrays(account_payload, count=1)
        authorization_payload = _decrypt_descriptor(encrypted_authorization, local_key)
        block_id, offset = _read_i32(authorization_payload, 0)
        if block_id != 75:
            raise TdataParseRejected()
        (serialized_authorization,) = _read_qbytearrays(authorization_payload[offset:], count=1)
        user_id, dc_id, auth_key = _read_authorization(serialized_authorization)
        return ParsedTdata(local_key=local_key, user_id=user_id, dc_id=dc_id, auth_key=auth_key)
    except TdataPasscodeRejected:
        raise
    except (OSError, RuntimeError, ValueError):
        raise TdataParseRejected() from None


def _read_tdf(path) -> tuple[bytes, int]:
    data = path.read_bytes()
    if len(data) < 8 + 16 or data[:4] != _TDF_MAGIC:
        raise TdataParseRejected()
    version = int.from_bytes(data[4:8], "little")
    payload, checksum = data[8:-16], data[-16:]
    expected = hashlib.md5(
        payload + len(payload).to_bytes(4, "little") + version.to_bytes(4, "little") + _TDF_MAGIC
    ).digest()
    if not payload or checksum != expected:
        raise TdataParseRejected()
    return payload, version


def _read_tdf_with_suffixes(root, file_name: str) -> tuple[bytes, int]:
    for suffix in ("s", "1", "0"):
        try:
            return _read_tdf(root / f"{file_name}{suffix}")
        except TdataParseRejected:
            continue
    raise TdataParseRejected()


def _read_qbytearrays(data: bytes, *, count: int) -> tuple[bytes, ...]:
    values: list[bytes] = []
    offset = 0
    for _ in range(count):
        if len(data) - offset < 4:
            raise TdataParseRejected()
        length = int.from_bytes(data[offset : offset + 4], "big")
        offset += 4
        if length == 0xFFFFFFFF or length > len(data) - offset:
            raise TdataParseRejected()
        values.append(data[offset : offset + length])
        offset += length
    if offset != len(data):
        raise TdataParseRejected()
    return tuple(values)


def _derive_local_key(salt: bytes, passcode: str) -> bytes:
    passcode_bytes = passcode.encode("utf-8")
    material = hashlib.sha512(salt + passcode_bytes + salt).digest()
    iterations = 100_000 if passcode_bytes else 1
    return hashlib.pbkdf2_hmac("sha512", material, salt, iterations, _LOCAL_KEY_BYTES)


def _read_active_account_index(info: bytes) -> int:
    count, offset = _read_i32(info, 0)
    if count < 1 or count > 3:
        raise TdataParseRejected()
    indices: list[int] = []
    for _ in range(count):
        index, offset = _read_i32(info, offset)
        if index < 0 or index > 2:
            raise TdataParseRejected()
        indices.append(index)
    active_index = indices[0]
    if offset < len(info):
        active_index, offset = _read_i32(info, offset)
    if offset != len(info) or active_index not in indices:
        raise TdataParseRejected()
    return active_index


def _account_file_name(index: int) -> str:
    name = "data" if index == 0 else f"data#{index + 1}"
    return "".join(f"{byte & 0x0F:X}{byte >> 4:X}" for byte in hashlib.md5(name.encode("utf-8")).digest())


def _read_authorization(payload: bytes) -> tuple[int, int, bytes]:
    user_id, offset = _read_i32(payload, 0)
    dc_id, offset = _read_i32(payload, offset)
    if user_id <= 0 or dc_id <= 0:
        raise TdataParseRejected()
    key_count, offset = _read_i32(payload, offset)
    if key_count < 1 or key_count > 20:
        raise TdataParseRejected()
    main_auth_key: bytes | None = None
    for _ in range(key_count):
        key_dc_id, offset = _read_i32(payload, offset)
        if key_dc_id <= 0 or len(payload) - offset < _LOCAL_KEY_BYTES:
            raise TdataParseRejected()
        key = payload[offset : offset + _LOCAL_KEY_BYTES]
        offset += _LOCAL_KEY_BYTES
        if key_dc_id == dc_id:
            main_auth_key = key
    keys_to_destroy, offset = _read_i32(payload, offset)
    if keys_to_destroy < 0 or keys_to_destroy > 20:
        raise TdataParseRejected()
    expected_remaining = keys_to_destroy * (4 + _LOCAL_KEY_BYTES)
    if len(payload) - offset != expected_remaining or main_auth_key is None:
        raise TdataParseRejected()
    return user_id, dc_id, main_auth_key


def _read_i32(data: bytes, offset: int) -> tuple[int, int]:
    if len(data) - offset < 4:
        raise TdataParseRejected()
    return int.from_bytes(data[offset : offset + 4], "big", signed=True), offset + 4


def _decrypt_descriptor(encrypted: bytes, auth_key: bytes) -> bytes:
    if len(encrypted) <= _AES_BLOCK_BYTES or len(encrypted) % _AES_BLOCK_BYTES:
        raise TdataParseRejected()
    message_key, ciphertext = encrypted[:_AES_BLOCK_BYTES], encrypted[_AES_BLOCK_BYTES:]
    aes_key, aes_iv = _derive_aes_key_and_iv(auth_key, message_key)
    plaintext = _aes_ige_decrypt(ciphertext, aes_key, aes_iv)
    if hashlib.sha1(plaintext).digest()[:_AES_BLOCK_BYTES] != message_key:
        raise TdataPasscodeRejected()
    payload_size = int.from_bytes(plaintext[:4], "little")
    if payload_size < 4 or payload_size > len(plaintext):
        raise TdataParseRejected()
    return plaintext[4:payload_size]


def _derive_aes_key_and_iv(auth_key: bytes, message_key: bytes) -> tuple[bytes, bytes]:
    if len(auth_key) != _LOCAL_KEY_BYTES or len(message_key) != _AES_BLOCK_BYTES:
        raise TdataParseRejected()
    offset = 8
    sha1_a = hashlib.sha1(message_key + auth_key[offset : offset + 32]).digest()
    sha1_b = hashlib.sha1(
        auth_key[offset + 32 : offset + 48] + message_key + auth_key[offset + 48 : offset + 64]
    ).digest()
    sha1_c = hashlib.sha1(auth_key[offset + 64 : offset + 96] + message_key).digest()
    sha1_d = hashlib.sha1(message_key + auth_key[offset + 96 : offset + 128]).digest()
    aes_key = sha1_a[:8] + sha1_b[8:20] + sha1_c[4:16]
    aes_iv = sha1_a[8:20] + sha1_b[:8] + sha1_c[16:20] + sha1_d[:8]
    return aes_key, aes_iv


def _aes_ige_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    if len(ciphertext) % _AES_BLOCK_BYTES or len(iv) != _AES_BLOCK_BYTES * 2:
        raise TdataParseRejected()
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    previous_ciphertext, previous_plaintext = iv[:16], iv[16:]
    plaintext = bytearray()
    for offset in range(0, len(ciphertext), _AES_BLOCK_BYTES):
        block = ciphertext[offset : offset + _AES_BLOCK_BYTES]
        decrypted = decryptor.update(bytes(left ^ right for left, right in zip(block, previous_plaintext)))
        current = bytes(left ^ right for left, right in zip(decrypted, previous_ciphertext))
        plaintext.extend(current)
        previous_ciphertext, previous_plaintext = block, current
    return bytes(plaintext)
