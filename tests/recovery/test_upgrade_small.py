"""upgrade_small_ia_images: upgrade candidate detection (larger + valid) and
dry-run candidate emission. All offline (download is faked)."""

import json

from recovery.lib.config import RecoveryConfig
from recovery.lib.manifest import ManifestWriter
from recovery.lib.images import tiny_png_bytes
from recovery import upgrade_small_ia_images as upg

H1 = "a" * 32
H2 = "b" * 32


def _fake_download(new_bytes, sha="0" * 64):
    def _fake(url, dest, config, hash=None):
        # mimic download_image: write the payload to dest on success
        payload = tiny_png_bytes(width=4, height=4)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return {
            "url": url,
            "status": "ok",
            "mime": "image/png",
            "bytes": new_bytes,
            "content_sha256": sha,
            "derived_sha256": "1" * 64,
        }

    return _fake


def _small_manifest(tmp_path):
    p = tmp_path / "small.tsv"
    with ManifestWriter(p) as w:
        w.add(hash=H1, source_url="https://i.sstatic.net/AbC12.png",
              source_class="ia_stack_imgur", status="recovered")
        w.add(hash=H2, source_url="https://i.sstatic.net/XyZ99.png",
              source_class="ia_stack_imgur", status="recovered")
    return p


def test_upgrade_candidate_detection(tmp_path, monkeypatch):
    """Larger + valid -> upgrade_candidate; smaller -> no_improvement."""
    items = [
        {"hash": H1, "source_url": "https://i.sstatic.net/AbC12.png",
         "old_bytes": 100, "old_sha256": "a" * 64},
        {"hash": H2, "source_url": "https://i.sstatic.net/XyZ99.png",
         "old_bytes": 100, "old_sha256": "b" * 64},
    ]
    out_dir = tmp_path / "upgraded"

    def _alternating(url, dest, config, hash=None):
        big = url.endswith("AbC12.png")
        return _fake_download(900 if big else 50)(url, dest, config, hash=hash)

    config = RecoveryConfig().with_overrides(fetch=True, dry_run=False)
    rows = upg.upgrade_items(items, config, out_dir, stage_dir=None,
                             download=_alternating)
    by_hash = {r["hash"]: r for r in rows}
    assert by_hash[H1]["status"] == "upgrade_candidate"
    assert by_hash[H1]["new_bytes"] == 900
    assert by_hash[H1]["old_bytes"] == 100
    assert by_hash[H1]["new_sha256"] == "0" * 64
    assert by_hash[H2]["status"] == "no_improvement"
    # payload written for the upgrade, cleaned up for the non-improvement
    assert (out_dir / H1).is_file()
    assert not (out_dir / H2).exists()


def test_dryrun_emits_candidates_without_network(tmp_path):
    """Dry-run main: candidate rows, no download calls, no out-dir writes."""
    small = _small_manifest(tmp_path)
    out_manifest = tmp_path / "upgrade.tsv"
    out_dir = tmp_path / "upgraded"

    rc = upg.main(
        [
            "--small-manifest", str(small),
            "--out-upgrade-manifest", str(out_manifest),
            "--out-dir", str(out_dir),
        ]
    )
    assert rc == 0
    assert out_manifest.is_file()
    assert not out_dir.exists()

    lines = [line.rstrip("\n").split("\t") for line in
             open(out_manifest, encoding="utf-8") if line.strip()]
    header, rows = lines[0], lines[1:]
    assert len(rows) == 2
    assert all(row[6] == "candidate" for row in rows)  # status column
    assert {row[0] for row in rows} == {H1, H2}


def test_upgrade_main_fetch_with_fake(tmp_path, monkeypatch):
    """main() with --fetch --no-dry-run uses the (faked) downloader."""
    small = _small_manifest(tmp_path)
    out_manifest = tmp_path / "upgrade.tsv"
    out_dir = tmp_path / "upgraded"
    monkeypatch.setattr(upg, "download_image", _fake_download(1500))

    rc = upg.main(
        [
            "--small-manifest", str(small),
            "--out-upgrade-manifest", str(out_manifest),
            "--out-dir", str(out_dir),
            "--stage-images-dir", str(tmp_path / "stage"),
            "--fetch", "--no-dry-run",
        ]
    )
    assert rc == 0
    assert (out_dir / H1).is_file()
    assert (out_dir / H2).is_file()
    rows = [line.rstrip("\n").split("\t") for line in
            open(out_manifest, encoding="utf-8") if line.strip()][1:]
    assert all(row[6] == "upgrade_candidate" for row in rows)