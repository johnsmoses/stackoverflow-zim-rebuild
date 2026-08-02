"""Manifest writer/reader round-trip + (hash, source_url) dedup (H8)."""

import json

import pytest

from recovery.lib.manifest import (
    SCHEMA_VERSION,
    TSV_FIELDS,
    ManifestReader,
    ManifestWriter,
)

H1 = "a" * 32
H2 = "b" * 32
URL_A = "https://i.sstatic.net/AbC12.png"
URL_B = "https://i.stack.imgur.com/AbC12.png"


@pytest.fixture
def tsv_path(tmp_path):
    return tmp_path / "m.tsv"


@pytest.fixture
def jsonl_path(tmp_path):
    return tmp_path / "m.jsonl"


def _write_sample(path, fmt):
    with ManifestWriter(path, fmt=fmt) as w:
        assert w.add(hash=H1, source_url=URL_A, source_class="stage",
                     status="candidate") is True
        assert w.add(hash=H1, source_url=URL_B, source_class="xml_dump",
                     status="candidate") is True
        assert w.add(hash=H2, source_url=URL_A, source_class="stage",
                     status="candidate") is True
        # duplicate (hash, source_url) must be rejected (H8)
        assert w.add(hash=H1, source_url=URL_A, source_class="stage",
                     status="candidate") is False
        assert w.duplicates == 1
        assert w.row_count == 3


def test_tsv_roundtrip_and_dedup(tsv_path):
    _write_sample(tsv_path, "tsv")
    rows = ManifestReader(tsv_path).read_all()
    assert len(rows) == 3
    assert rows[0]["schema_version"] == SCHEMA_VERSION
    assert rows[0]["hash"] == H1
    assert rows[0]["source_url"] == URL_A
    # one hash, two sources -> two rows (H8)
    assert [r["source_url"] for r in rows if r["hash"] == H1] == [URL_A, URL_B]
    assert all(set(r) == set(TSV_FIELDS) for r in rows)
    assert ManifestReader(tsv_path).pairs() == {
        (H1, URL_A), (H1, URL_B), (H2, URL_A)
    }


def test_jsonl_roundtrip(jsonl_path):
    _write_sample(jsonl_path, "jsonl")
    rows = ManifestReader(jsonl_path).read_all()
    assert len(rows) == 3
    assert rows[0]["schema_version"] == "1"
    # every JSONL line is a full schema object
    with jsonl_path.open() as fh:
        for line in fh:
            obj = json.loads(line)
            assert set(obj) == set(TSV_FIELDS)


def test_append_is_atomic_and_keeps_existing(tmp_path):
    path = tmp_path / "a.tsv"
    with ManifestWriter(path) as w:
        w.add(hash=H1, source_url=URL_A, source_class="stage")
    with ManifestWriter(path) as w:
        assert w.add(hash=H2, source_url=URL_A, source_class="stage") is True
        assert w.add(hash=H1, source_url=URL_A, source_class="stage") is False
    rows = ManifestReader(path).read_all()
    assert len(rows) == 2
    assert {r["hash"] for r in rows} == {H1, H2}