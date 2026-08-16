from pathlib import Path

import pytest

from telegram_connector.importers.tdata import preflight
from telegram_connector.importers.tdata.preflight import (
    TdataSourceChanged,
    TdataSourceRejected,
    prepare_tdata_copy,
)


def make_tdata(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "key_datas").write_bytes(b"synthetic-key-metadata")
    (root / "D877F783D5D3EF8C").mkdir()
    (root / "D877F783D5D3EF8C" / "map0").write_bytes(b"synthetic-account-data")
    return root


def test_preflight_copies_a_supported_tdata_tree_without_changing_source(tmp_path: Path) -> None:
    source = make_tdata(tmp_path / "source" / "tdata")
    destination = tmp_path / "private-copy"

    snapshot = prepare_tdata_copy(source, destination, max_bytes=1_000_000)

    assert snapshot.root == destination
    assert snapshot.total_bytes == len(b"synthetic-key-metadata") + len(b"synthetic-account-data")
    assert (destination / "key_datas").read_bytes() == b"synthetic-key-metadata"
    assert (source / "D877F783D5D3EF8C" / "map0").read_bytes() == b"synthetic-account-data"
    snapshot.assert_source_unchanged()


def test_preflight_rejects_tdata_without_key_datas(tmp_path: Path) -> None:
    source = tmp_path / "source" / "tdata"
    source.mkdir(parents=True)
    (source / "D877F783D5D3EF8C").mkdir()

    with pytest.raises(TdataSourceRejected, match="^unsupported tdata source$"):
        prepare_tdata_copy(source, tmp_path / "private-copy", max_bytes=1_000_000)


def test_preflight_detects_source_mutation_after_the_copy(tmp_path: Path) -> None:
    source = make_tdata(tmp_path / "source" / "tdata")
    snapshot = prepare_tdata_copy(source, tmp_path / "private-copy", max_bytes=1_000_000)
    (source / "D877F783D5D3EF8C" / "map0").write_bytes(b"mutated")

    with pytest.raises(TdataSourceChanged, match="^tdata source changed$"):
        snapshot.assert_source_unchanged()


def test_preflight_rejects_a_remote_drive_before_reading_tdata(tmp_path: Path, monkeypatch) -> None:
    source = make_tdata(tmp_path / "source" / "tdata")
    monkeypatch.setattr(preflight, "_windows_drive_type", lambda _: 4)

    with pytest.raises(TdataSourceRejected, match="^unsupported tdata source$"):
        prepare_tdata_copy(source, tmp_path / "private-copy", max_bytes=1_000_000)


def test_preflight_rejects_a_file_handle_that_resolves_outside_source(tmp_path: Path, monkeypatch) -> None:
    source = make_tdata(tmp_path / "source" / "tdata")
    monkeypatch.setattr(preflight, "_opened_path_is_inside", lambda *_: False)

    with pytest.raises(TdataSourceRejected, match="^unsupported tdata source$"):
        prepare_tdata_copy(source, tmp_path / "private-copy", max_bytes=1_000_000)
