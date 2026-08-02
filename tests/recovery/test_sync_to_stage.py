"""Validated sync into the stage: placeholder targets replaced, real targets
preserved, invalid sources rejected. All offline."""

import json

from recovery.lib.images import sha256_of, tiny_png_bytes
from recovery.lib.manifest import ManifestWriter
from recovery.lib.placeholders import write_placeholder_for
from recovery.sync_to_stage import main

H1 = "a" * 32
H2 = "b" * 32
H3 = "c" * 32
H4 = "d" * 32


def _write_spec(tmp_path, versioned, sha="", size=0):
    spec = {"size_bytes": 1852, "format": "webp", "sha256": None}
    if versioned:
        spec = {"size_bytes": size, "format": "webp", "sha256": sha}
    p = tmp_path / "placeholder-spec.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    return p


def _recovery_manifest(tmp_path, entries):
    """entries: list of (hash, url, status, sha)."""
    path = tmp_path / "recovered.tsv"
    with ManifestWriter(path) as w:
        for h, url, status, sha in entries:
            w.add(hash=h, source_url=url, source_class="edge",
                  status=status, content_sha256=sha)
    return path


def _stage_placeholder(tmp_path, spec_path, hash, versioned_spec):
    """Write a placeholder into stage/images; returns the versioned spec dict."""
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    stage = tmp_path / "stage"
    info = write_placeholder_for(hash, stage, spec)
    if versioned_spec:
        spec["sha256"] = info["content_sha256"]
        spec["size_bytes"] = info["bytes"]
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return spec


def _run_sync(tmp_path, manifest, source_dir, stage_dir, spec_path, dry_run=True):
    return main(
        [
            "--recovery-manifest", str(manifest),
            "--source-dir", str(source_dir),
            "--stage-images-dir", str(stage_dir),
            "--placeholder-spec", str(spec_path),
            "--out", str(tmp_path / "sync-applied.jsonl"),
            "--out-skipped", str(tmp_path / "sync-skipped.jsonl"),
        ]
        + (["--dry-run"] if dry_run else ["--no-dry-run"]),
    )


def _read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def test_recovered_replaces_placeholder_target(tmp_path, capsys):
    """A recovered image overwrites a confirmed-placeholder stage target."""
    source_dir = tmp_path / "recovered-images"
    stage_dir = tmp_path / "stage" / "images"
    source_dir.mkdir(parents=True)
    stage_dir.mkdir(parents=True)

    data = tiny_png_bytes()
    (source_dir / H1).write_bytes(data)
    spec_path = _write_spec(tmp_path, versioned=True)
    _stage_placeholder(tmp_path, spec_path, H1, versioned_spec=True)
    assert (stage_dir / H1).read_bytes() != data

    manifest = _recovery_manifest(tmp_path, [(H1, "https://edge.example/x.png",
                                              "ok", sha256_of(data))])
    rc = _run_sync(tmp_path, manifest, source_dir, stage_dir, spec_path,
                   dry_run=False)
    assert rc == 0
    assert (stage_dir / H1).read_bytes() == data

    applied = _read_jsonl(tmp_path / "sync-applied.jsonl")
    skipped = _read_jsonl(tmp_path / "sync-skipped.jsonl")
    assert len(applied) == 1 and applied[0]["hash"] == H1
    assert applied[0]["reason"] == "placeholder-target"
    assert skipped == []
    # before/after placeholder counts reported on stderr
    err = capsys.readouterr().err
    assert "placeholders_before=1" in err and "placeholders_after=0" in err


def test_real_target_is_never_overwritten(tmp_path):
    """A valid non-placeholder stage file is preserved (already-real)."""
    source_dir = tmp_path / "recovered-images"
    stage_dir = tmp_path / "stage" / "images"
    source_dir.mkdir(parents=True)
    stage_dir.mkdir(parents=True)

    recovered = tiny_png_bytes(width=6, height=6)
    existing = tiny_png_bytes(width=8, height=8)
    (source_dir / H1).write_bytes(recovered)
    (stage_dir / H1).write_bytes(existing)
    spec_path = _write_spec(tmp_path, versioned=False)

    manifest = _recovery_manifest(tmp_path, [(H1, "https://edge.example/x.png",
                                              "ok", sha256_of(recovered))])
    rc = _run_sync(tmp_path, manifest, source_dir, stage_dir, spec_path,
                   dry_run=False)
    assert rc == 0
    assert (stage_dir / H1).read_bytes() == existing  # untouched
    skipped = _read_jsonl(tmp_path / "sync-skipped.jsonl")
    assert len(skipped) == 1 and skipped[0]["reason"] == "already-real"


def test_rejects_html_source(tmp_path):
    """A malformed/HTML 'recovered' file is rejected (H6)."""
    source_dir = tmp_path / "recovered-images"
    stage_dir = tmp_path / "stage" / "images"
    source_dir.mkdir(parents=True)
    stage_dir.mkdir(parents=True)

    (source_dir / H1).write_bytes(b"<html><body>not an image</body></html>")
    spec_path = _write_spec(tmp_path, versioned=False)
    manifest = _recovery_manifest(
        tmp_path, [(H1, "https://edge.example/x.html", "ok", "0" * 64)]
    )
    rc = _run_sync(tmp_path, manifest, source_dir, stage_dir, spec_path,
                   dry_run=False)
    assert rc == 0
    assert not (stage_dir / H1).exists()
    skipped = _read_jsonl(tmp_path / "sync-skipped.jsonl")
    assert len(skipped) == 1 and skipped[0]["reason"] == "missing-source"


def test_skips_sha_mismatch(tmp_path):
    """Source whose sha does not match the manifest row is skipped."""
    source_dir = tmp_path / "recovered-images"
    stage_dir = tmp_path / "stage" / "images"
    source_dir.mkdir(parents=True)
    stage_dir.mkdir(parents=True)

    data = tiny_png_bytes()
    (source_dir / H1).write_bytes(data)
    spec_path = _write_spec(tmp_path, versioned=False)
    manifest = _recovery_manifest(
        tmp_path, [(H1, "https://edge.example/x.png", "ok", "f" * 64)]
    )
    rc = _run_sync(tmp_path, manifest, source_dir, stage_dir, spec_path,
                   dry_run=False)
    assert rc == 0
    assert not (stage_dir / H1).exists()
    skipped = _read_jsonl(tmp_path / "sync-skipped.jsonl")
    assert len(skipped) == 1 and skipped[0]["reason"] == "sha-mismatch"