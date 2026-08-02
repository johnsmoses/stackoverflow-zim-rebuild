"""finalize_unavailable: writes versioned placeholders for still-pending
hashes and is idempotent on re-run. All offline."""

import json

from recovery.finalize_unavailable import main

H1 = "a" * 32
H2 = "b" * 32
H3 = "c" * 32

CLASSIFIED_REMAINING = (
    "hash\tsource_url\tsource_class\tstatus\tpage\n"
    f"{H1}\thttps://edge.example/one.png\tother_http\tmissing\tp1\n"
    f"{H2}\thttps://edge.example/two.png\tother_http\tmissing\tp2\n"
    f"{H3}\t\tno_original_url\tmissing\tp3\n"
)


def _setup(tmp_path):
    remaining = tmp_path / "classified-remaining.tsv"
    remaining.write_text(CLASSIFIED_REMAINING, encoding="utf-8")
    stage_dir = tmp_path / "stage" / "images"
    stage_dir.mkdir(parents=True)
    spec = tmp_path / "placeholder-spec.json"
    spec.write_text(
        json.dumps({"size_bytes": 1852, "format": "webp", "sha256": None}),
        encoding="utf-8",
    )
    return remaining, stage_dir, spec


def _run(tmp_path, remaining, stage_dir, spec):
    out = tmp_path / "finalize-log.jsonl"
    rc = main(
        [
            "--classified-remaining", str(remaining),
            "--stage-images-dir", str(stage_dir),
            "--placeholder-spec", str(spec),
            "--out", str(out),
            "--no-dry-run",
        ]
    )
    return rc, out


def test_finalize_writes_and_is_idempotent(tmp_path):
    remaining, stage_dir, spec = _setup(tmp_path)
    rc, out = _run(tmp_path, remaining, stage_dir, spec)
    assert rc == 0

    # every pending hash now has a stage file (the key invariant)
    for h in (H1, H2, H3):
        assert (stage_dir / h).is_file()
        assert (stage_dir / h).stat().st_size > 0

    log = json.loads(out.read_text(encoding="utf-8").strip().splitlines()[0])
    assert log["status"] == "written"
    assert log["placeholder_sha256"]
    assert log["last_url"] == "https://edge.example/one.png"

    before = {h: (stage_dir / h).read_bytes() for h in (H1, H2, H3)}

    # re-run: idempotent, nothing changes, no pending hash left without a file
    rc2, out2 = _run(tmp_path, remaining, stage_dir, spec)
    assert rc2 == 0
    lines = [json.loads(line) for line in out2.read_text().strip().splitlines()]
    assert len(lines) == 3
    assert all(row["status"] == "already-placeholder" for row in lines)
    assert {h: (stage_dir / h).read_bytes() for h in (H1, H2, H3)} == before

    # placeholders round-trip with is_placeholder once the spec is versioned
    from recovery.lib.images import is_placeholder
    import hashlib

    for h in (H1, H2, H3):
        data = (stage_dir / h).read_bytes()
        versioned = {"size_bytes": len(data), "format": "webp",
                     "sha256": hashlib.sha256(data).hexdigest()}
        assert is_placeholder(stage_dir / h, versioned) is True


def test_finalize_never_overwrites_real_image(tmp_path):
    remaining, stage_dir, spec = _setup(tmp_path)
    real = tmp_path / "stage" / "images" / H1
    from recovery.lib.images import tiny_png_bytes

    real.write_bytes(tiny_png_bytes(width=8, height=8))
    rc, out = _run(tmp_path, remaining, stage_dir, spec)
    assert rc == 0
    lines = [json.loads(line) for line in out.read_text().strip().splitlines()]
    by_hash = {row["hash"]: row for row in lines}
    assert by_hash[H1]["status"] == "already-recovered"
    assert (stage_dir / H1).read_bytes() == tiny_png_bytes(width=8, height=8)