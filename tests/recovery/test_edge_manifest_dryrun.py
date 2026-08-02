"""rescue_edge_cases dry-run behaviour: candidate manifest emitted, zero
network, zero writes to --out-dir (H1/H9)."""

import subprocess
import sys
from pathlib import Path

from recovery.lib.manifest import ManifestReader

REPO_ROOT = Path(__file__).resolve().parents[2]

CLASSIFIED = (
    "hash\tsource_url\tsource_class\tstatus\tpage\n"
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\t"
    "https://camo.githubusercontent.com/abc/"
    "68747470733a2f2f692e737461636b2e696d6775722e636f6d2f41624331322e706e67\t"
    "other_http\tmissing\tpage1\n"
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\t"
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ\tother_http\tmissing\tpage2\n"
    "cccccccccccccccccccccccccccccccc\t"
    "https://travis-ci.org/octocat/Hello-World\tother_http\tmissing\tpage3\n"
)


def _write_classified(tmp_path):
    p = tmp_path / "classified.tsv"
    p.write_text(CLASSIFIED, encoding="utf-8")
    return p


def test_dryrun_emits_candidate_manifest_no_network(tmp_path):
    """In-process dry-run: manifest rows with status candidate; out-dir
    untouched. The socket-blocking fixture would fail any network attempt."""
    from recovery.rescue_edge_cases import main

    classified = _write_classified(tmp_path)
    out_manifest = tmp_path / "edge.tsv"
    out_dir = tmp_path / "out-images"

    rc = main(
        [
            "--classified", str(classified),
            "--out-manifest", str(out_manifest),
            "--out-dir", str(out_dir),
        ]
    )
    assert rc == 0
    assert out_manifest.is_file()
    rows = ManifestReader(out_manifest).read_all()
    assert len(rows) == 11  # camo(2) + youtube(5) + travis(4)
    assert all(r["status"] == "candidate" for r in rows)
    urls = [r["source_url"] for r in rows]

    # camo decoded origin is a candidate
    assert "https://i.stack.imgur.com/AbC12.png" in urls
    # youtube thumbnail synthesis
    assert "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg" in urls
    # badge canonical
    assert (
        "https://api.travis-ci.org/octocat/Hello-World.svg?branch=master" in urls
    )
    # dry-run never touches --out-dir
    assert not out_dir.exists()


def test_cli_subprocess_dryrun(tmp_path):
    """CLI wiring end-to-end: python3 -m recovery.rescue_edge_cases."""
    classified = _write_classified(tmp_path)
    out_manifest = tmp_path / "edge.tsv"
    out_dir = tmp_path / "out-images"
    env = {"PYTHONPATH": str(REPO_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [
            sys.executable, "-m", "recovery.rescue_edge_cases",
            "--classified", str(classified),
            "--out-manifest", str(out_manifest),
            "--out-dir", str(out_dir),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert out_manifest.is_file()
    assert not out_dir.exists()
    assert "DONE" in result.stderr