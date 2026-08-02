"""Placeholder detection (H10), sniffing/rejection (H6), URL validation (H2),
config dry-run defaults (H1). All offline (socket-blocking fixture)."""

from recovery.lib.config import RecoveryConfig
from recovery.lib.images import (
    is_placeholder,
    is_valid_image,
    sniff_bytes,
    tiny_png_bytes,
    validate_url,
)

# --------------------------------------------------------------------------- #
# is_placeholder (H10)
# --------------------------------------------------------------------------- #

PLACEHOLDER_BYTES = 1852


def _spec_with(sha):
    return {"size_bytes": PLACEHOLDER_BYTES, "sha256": sha}


def test_is_placeholder_requires_size_and_sha(tmp_path):
    p = tmp_path / "img"
    p.write_bytes(b"\x00" * PLACEHOLDER_BYTES)
    sha = "deadbeef" * 8  # content hash of b"\x00"*1852 is not this

    # size ok + matching sha -> True
    import hashlib
    real_sha = hashlib.sha256(b"\x00" * PLACEHOLDER_BYTES).hexdigest()
    assert is_placeholder(p, _spec_with(real_sha)) is True

    # size ok + wrong sha -> False
    assert is_placeholder(p, _spec_with("0" * 64)) is False

    # wrong size -> False even with any sha
    assert is_placeholder(p, _spec_with(real_sha)) is True
    p2 = tmp_path / "img2"
    p2.write_bytes(b"\x00" * (PLACEHOLDER_BYTES + 1))
    assert is_placeholder(p2, _spec_with(real_sha)) is False

    # unversioned spec (sha256 null): size alone is NOT proof (H10)
    assert is_placeholder(p, {"size_bytes": PLACEHOLDER_BYTES, "sha256": None}) is False


def test_placeholder_roundtrip_via_writer(tmp_path):
    """write_placeholder_for -> record its sha in the spec -> is_placeholder."""
    import hashlib

    from recovery.lib.placeholders import write_placeholder_for

    spec = {"size_bytes": PLACEHOLDER_BYTES, "sha256": None, "format": "webp"}
    out_root = tmp_path / "stage"
    info = write_placeholder_for("c" * 32, out_root, spec)
    assert info["content_sha256"]
    assert (out_root / "images" / ("c" * 32)).is_file()
    assert info["bytes"] == (out_root / "images" / ("c" * 32)).stat().st_size

    versioned = dict(spec)
    versioned["sha256"] = info["content_sha256"]
    versioned["size_bytes"] = info["bytes"]
    assert is_placeholder(out_root / "images" / ("c" * 32), versioned) is True
    # and the actual sha is a real sha256 of the file
    assert hashlib.sha256(
        (out_root / "images" / ("c" * 32)).read_bytes()
    ).hexdigest() == info["content_sha256"]


# --------------------------------------------------------------------------- #
# sniff / reject (H6)
# --------------------------------------------------------------------------- #

def test_sniff_bytes():
    assert sniff_bytes(tiny_png_bytes()) == "image/png"
    assert sniff_bytes(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert sniff_bytes(b"GIF89a....") == "image/gif"
    assert sniff_bytes(b"RIFF\x10\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff_bytes(b"BM......") == "image/bmp"
    assert sniff_bytes(b"<svg xmlns=...") == "image/svg+xml"
    assert sniff_bytes(b"<html>") == "unknown"


def test_is_valid_image_rejects_html_xml_svg():
    assert is_valid_image(tiny_png_bytes()) is True
    assert is_valid_image(b"<html><body>not an image</body></html>") is False
    assert is_valid_image(b"<!DOCTYPE html><html>") is False
    assert is_valid_image(b"<?xml version='1.0'?><svg/>") is False
    assert is_valid_image(b"<svg width='10'></svg>") is False
    assert is_valid_image(b"") is False
    assert is_valid_image(b"garbage") is False


# --------------------------------------------------------------------------- #
# validate_url (H2)
# --------------------------------------------------------------------------- #

GLOBAL_V4 = "93.184.216.34"


def _resolver(*ips):
    return lambda host: list(ips)


def test_validate_url_rejects_bad_scheme_and_credentials():
    assert validate_url("ftp://i.stack.imgur.com/x.png") is False
    assert validate_url("file:///etc/passwd") is False
    assert validate_url("//i.stack.imgur.com/x.png") is False
    assert validate_url("http://user:pass@i.stack.imgur.com/x.png") is False
    assert validate_url("http://user@i.stack.imgur.com/x.png") is False


def test_validate_url_rejects_private_ip_literals():
    for addr in ("10.0.0.1", "127.0.0.1", "127.0.0.2", "192.168.1.1",
                 "172.16.0.1", "169.254.1.1", "0.0.0.0", "255.255.255.255"):
        assert validate_url(f"http://{addr}/img.png") is False, addr
    for addr in ("::1", "fe80::1", "fc00::1", "fd12::1", "ff02::1", "::"):
        assert validate_url(f"https://[{addr}]/img.png") is False, addr


def test_validate_url_resolves_to_global_only():
    # hostname resolving to private IP -> rejected
    assert validate_url(
        "http://evil.example/img.png", resolver=_resolver("10.0.0.5")
    ) is False
    # hostname resolving to a global IP -> accepted
    assert validate_url(
        "https://i.stack.imgur.com/AbC12.png", resolver=_resolver(GLOBAL_V4)
    ) is True
    # multiple addresses, any non-global -> rejected (fail closed)
    assert validate_url(
        "http://mixed.example/img.png", resolver=_resolver(GLOBAL_V4, "192.168.0.2")
    ) is False
    # unresolved host -> rejected
    assert validate_url(
        "http://nope.example/img.png", resolver=lambda host: []
    ) is False


# --------------------------------------------------------------------------- #
# config dry-run defaults (H1)
# --------------------------------------------------------------------------- #

def test_config_dry_run_default():
    cfg = RecoveryConfig()
    assert cfg.dry_run is True
    assert cfg.fetch is False
    assert cfg.fetch_ok is False  # no sockets without --fetch


def test_config_fetch_requires_non_dry_run():
    cfg = RecoveryConfig().with_overrides(fetch=True)
    assert cfg.fetch_ok is False  # fetch + default dry-run => still no sockets
    cfg2 = RecoveryConfig().with_overrides(fetch=True, dry_run=False)
    assert cfg2.fetch_ok is True


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("RECOVERY_FETCH", "1")
    monkeypatch.setenv("RECOVERY_DELAY", "1.5")
    monkeypatch.setenv("RECOVERY_PLACEHOLDER_BYTES", "1852")
    cfg = RecoveryConfig.from_env()
    assert cfg.fetch is True
    assert cfg.delay == 1.5
    assert cfg.placeholder_bytes == 1852
    assert cfg.fetch_ok is False  # dry-run still on

    monkeypatch.setenv("RECOVERY_DRY_RUN", "0")
    cfg2 = RecoveryConfig.from_env()
    assert cfg2.fetch_ok is True


def test_config_no_hardcoded_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("RECOVERY_WORK_ROOT", str(tmp_path))
    cfg = RecoveryConfig.from_env().resolved()
    assert cfg.work_root == tmp_path.resolve()
    assert cfg.stage_dir == tmp_path.resolve() / "stage"
    assert cfg.out_dir == tmp_path.resolve() / "out"
    assert cfg.asset_cache_dir == tmp_path.resolve() / "assets"