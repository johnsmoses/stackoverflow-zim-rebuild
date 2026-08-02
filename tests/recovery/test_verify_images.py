"""verify_images: PASS/FAIL report correctness on a fixture. All offline."""

import json

from recovery.lib.images import tiny_png_bytes
from recovery.lib.manifest import ManifestWriter
from recovery.lib.placeholders import write_placeholder_for
from recovery.verify_images import main, verify_stage

H1 = "a" * 32
H2 = "b" * 32
H3 = "c" * 32


def _spec(tmp_path, sha=None, size=1852):
    p = tmp_path / "placeholder-spec.json"
    p.write_text(
        json.dumps({"size_bytes": size, "format": "webp", "sha256": sha}),
        encoding="utf-8",
    )
    return p


def _manifest(tmp_path, entries):
    path = tmp_path / "recovered.tsv"
    with ManifestWriter(path) as w:
        for h, status in entries:
            w.add(hash=h, source_url=f"https://edge.example/{h[:5]}.png",
                  source_class="edge", status=status)
    return path


def test_verify_pass(tmp_path):
    stage = tmp_path / "stage" / "images"
    stage.mkdir(parents=True)
    for h in (H1, H2):
        (stage / h).write_bytes(tiny_png_bytes(width=6, height=6))
    spec = _spec(tmp_path)
    manifest = _manifest(tmp_path, [(H1, "ok"), (H2, "upgrade_candidate")])
    out = tmp_path / "report.txt"

    rc = main(
        ["--stage-images-dir", str(stage), "--manifest", str(manifest),
         "--placeholder-spec", str(spec), "--out", str(out), "--sample", "1"]
    )
    assert rc == 0
    report = out.read_text(encoding="utf-8")
    assert "RESULT: PASS" in report
    assert "hashes_checked: 2" in report
    assert "placeholders_remaining: 0" in report


def test_verify_fail_reports_problems(tmp_path):
    stage = tmp_path / "stage" / "images"
    stage.mkdir(parents=True)
    # H1: recovered but file missing
    # H2: recovered but stage file is a confirmed placeholder
    spec_path = _spec(tmp_path)
    info = write_placeholder_for(H2, tmp_path / "stage", json.loads(
        spec_path.read_text(encoding="utf-8")))
    versioned = {"size_bytes": info["bytes"], "format": "webp",
                 "sha256": info["content_sha256"]}
    spec_path.write_text(json.dumps(versioned), encoding="utf-8")
    # H3: candidate row, stage file is HTML (invalid image)
    (stage / H3).write_bytes(b"<html><body>nope</body></html>")

    manifest = _manifest(tmp_path, [(H1, "ok"), (H2, "ok"), (H3, "candidate")])
    stats = verify_stage(stage, manifest, json.loads(
        spec_path.read_text(encoding="utf-8")), sample_n=0)
    assert stats["pass"] is False
    issues = {p["issue"] for p in stats["problems"]}
    assert "missing-file" in issues      # H1
    assert "placeholder" in issues       # H2 (recovered entry)
    assert "invalid-image" in issues     # H3
    assert stats["placeholders_remaining"] >= 1

    out = tmp_path / "report.txt"
    rc = main(
        ["--stage-images-dir", str(stage), "--manifest", str(manifest),
         "--placeholder-spec", str(spec_path), "--out", str(out)]
    )
    assert rc == 1
    report = out.read_text(encoding="utf-8")
    assert "RESULT: FAIL" in report
    assert "missing-file" in report and "invalid-image" in report