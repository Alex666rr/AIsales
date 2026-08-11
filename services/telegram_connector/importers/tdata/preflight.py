"""No-network validation and private copying for a Telegram Desktop tdata folder."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path


class TdataSourceRejected(ValueError):
    """Raised when a supplied tdata directory cannot be copied safely."""

    def __init__(self) -> None:
        super().__init__("unsupported tdata source")


class TdataSourceChanged(ValueError):
    """Raised when the source directory no longer matches its captured snapshot."""

    def __init__(self) -> None:
        super().__init__("tdata source changed")


@dataclass(frozen=True, slots=True)
class TdataSnapshot:
    """A private copy plus an integrity fingerprint of the original source tree."""

    root: Path
    digest: bytes
    total_bytes: int
    format_variant: str
    _source: Path = field(repr=False, compare=False)
    _source_root_identity: str = field(repr=False, compare=False)
    _max_bytes: int = field(repr=False, compare=False)

    def assert_source_unchanged(self) -> None:
        """Fail closed when the source tree differs from the captured source."""
        try:
            current_digest, _, _ = _snapshot_tree(
                self._source,
                max_bytes=self._max_bytes,
                source_root=self._source,
                source_root_identity=self._source_root_identity,
            )
        except TdataSourceRejected as error:
            raise TdataSourceChanged() from error
        if current_digest != self.digest:
            raise TdataSourceChanged()


def prepare_tdata_copy(source: Path, destination: Path, *, max_bytes: int) -> TdataSnapshot:
    """Copy one supported local tdata folder without following links or reparse points."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    source = Path(source)
    destination = Path(destination)
    _require_local_path(source)
    _require_local_path(destination)
    if destination.exists() or destination.is_symlink() or _is_reparse_point(destination):
        raise TdataSourceRejected()
    source_root_identity = _captured_root_identity(source)
    digest, total_bytes, files = _snapshot_tree(
        source,
        max_bytes=max_bytes,
        source_root=source,
        source_root_identity=source_root_identity,
    )
    try:
        destination.mkdir(parents=True, exist_ok=False)
        for relative_path in files:
            source_file = source / relative_path
            destination_file = destination / relative_path
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            _copy_regular_file(
                source_file,
                destination_file,
                source_root=source,
                source_root_identity=source_root_identity,
                max_bytes=max_bytes,
            )
        copied_digest, copied_total, _ = _snapshot_tree(
            destination,
            max_bytes=max_bytes,
            source_root=destination,
            source_root_identity=_captured_root_identity(destination),
        )
        if copied_digest != digest or copied_total != total_bytes:
            raise TdataSourceRejected()
        snapshot = TdataSnapshot(
            root=destination,
            digest=digest,
            total_bytes=total_bytes,
            format_variant="telegram-desktop-tdata",
            _source=source,
            _source_root_identity=source_root_identity,
            _max_bytes=max_bytes,
        )
        snapshot.assert_source_unchanged()
        return snapshot
    except (OSError, RuntimeError, ValueError) as error:
        shutil.rmtree(destination, ignore_errors=True)
        if isinstance(error, (TdataSourceRejected, TdataSourceChanged)):
            raise
        raise TdataSourceRejected() from error


def _snapshot_tree(
    root: Path,
    *,
    max_bytes: int,
    source_root: Path | None = None,
    source_root_identity: str | None = None,
) -> tuple[bytes, int, tuple[Path, ...]]:
    try:
        _require_directory(root)
        files: list[Path] = []
        total_bytes = 0
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
            relative_path = path.relative_to(root)
            _reject_special_path(path)
            if path.is_dir():
                continue
            path_stat = path.lstat()
            if not stat.S_ISREG(path_stat.st_mode):
                raise TdataSourceRejected()
            total_bytes += path_stat.st_size
            if total_bytes > max_bytes:
                raise TdataSourceRejected()
            digest.update(relative_path.as_posix().encode("utf-8"))
            digest.update(b"\x00")
            digest.update(
                _read_regular_file(
                    path,
                    max_bytes=path_stat.st_size,
                    source_root=source_root,
                    source_root_identity=source_root_identity,
                )
            )
            files.append(relative_path)
        if not files or Path("key_datas") not in files:
            raise TdataSourceRejected()
        return digest.digest(), total_bytes, tuple(files)
    except (OSError, RuntimeError, ValueError) as error:
        if isinstance(error, TdataSourceRejected):
            raise
        raise TdataSourceRejected() from error


def _require_directory(path: Path) -> None:
    _reject_special_path(path)
    path_stat = path.lstat()
    if not stat.S_ISDIR(path_stat.st_mode):
        raise TdataSourceRejected()


def _require_local_path(path: Path) -> None:
    """Reject UNC and Windows network-drive paths before any source traversal."""
    rendered = os.fspath(path)
    if rendered.startswith("\\\\"):
        raise TdataSourceRejected()
    if _windows_drive_type(path) == 4:  # DRIVE_REMOTE
        raise TdataSourceRejected()


def _windows_drive_type(path: Path) -> int | None:
    if os.name != "nt":
        return None
    import ctypes

    absolute = os.path.abspath(os.fspath(path))
    return int(ctypes.windll.kernel32.GetDriveTypeW(absolute))


def _reject_special_path(path: Path) -> None:
    path_stat = path.lstat()
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if stat.S_ISLNK(path_stat.st_mode) or bool(getattr(path_stat, "st_file_attributes", 0) & reparse_point):
        raise TdataSourceRejected()


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _read_regular_file(
    path: Path,
    *,
    max_bytes: int,
    source_root: Path | None = None,
    source_root_identity: str | None = None,
) -> bytes:
    descriptor: int | None = None
    try:
        before = path.lstat()
        _reject_special_path(path)
        if source_root is not None:
            _require_immediate_parent_chain(path, source_root)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size != before.st_size or opened.st_size > max_bytes:
            raise TdataSourceRejected()
        if source_root_identity is not None and not _opened_path_is_inside(descriptor, source_root_identity):
            raise TdataSourceRejected()
        if source_root is not None:
            _require_immediate_parent_chain(path, source_root)
        data = bytearray()
        while len(data) <= max_bytes:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) != opened.st_size:
            raise TdataSourceRejected()
        return bytes(data)
    except (OSError, RuntimeError, ValueError) as error:
        if isinstance(error, TdataSourceRejected):
            raise
        raise TdataSourceRejected() from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _require_immediate_parent_chain(path: Path, source_root: Path) -> None:
    """Reject a changed parent component before a Windows path open can follow it."""
    root = Path(os.path.abspath(source_root))
    current = Path(os.path.abspath(path))
    try:
        relative = current.relative_to(root)
    except ValueError as error:
        raise TdataSourceRejected() from error
    candidate = root
    _require_directory(candidate)
    for part in relative.parts[:-1]:
        candidate = candidate / part
        _require_directory(candidate)
    resolved = Path(os.path.realpath(current))
    resolved_root = Path(os.path.realpath(root))
    if not resolved.is_relative_to(resolved_root):
        raise TdataSourceRejected()


def _captured_root_identity(source_root: Path) -> str:
    """Capture the resolved root before copying, so later path swaps cannot redefine it."""
    return os.path.normcase(os.path.realpath(os.path.abspath(source_root)))


def _opened_path_is_inside(descriptor: int, source_root_identity: str) -> bool:
    """Check the actual Windows file handle before reading any payload bytes."""
    if os.name != "nt":
        return True
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    handle = msvcrt.get_osfhandle(descriptor)
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    get_final_path.restype = wintypes.DWORD
    required = get_final_path(handle, None, 0, 0)
    if not required:
        return False
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = get_final_path(handle, buffer, len(buffer), 0)
    if not written or written >= len(buffer):
        return False
    opened_identity = os.path.normcase(_strip_extended_windows_prefix(buffer.value))
    try:
        return os.path.commonpath((opened_identity, source_root_identity)) == source_root_identity
    except ValueError:
        return False


def _strip_extended_windows_prefix(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _copy_regular_file(
    source: Path,
    destination: Path,
    *,
    source_root: Path,
    source_root_identity: str,
    max_bytes: int,
) -> None:
    data = _read_regular_file(
        source,
        max_bytes=max_bytes,
        source_root=source_root,
        source_root_identity=source_root_identity,
    )
    with destination.open("xb") as output:
        output.write(data)
