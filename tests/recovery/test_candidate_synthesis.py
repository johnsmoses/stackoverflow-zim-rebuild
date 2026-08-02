"""Candidate synthesis for the edge resolver: URL cleanup, camo decoding,
badge transforms, and special host transforms. All offline (the
socket-blocking fixture is active)."""

from urllib.parse import urlparse

from recovery.rescue_edge_cases import (
    BADGE_HOSTS,
    badge_candidates,
    clean_url,
    decode_camo,
    page_candidates_enhanced,
    special_candidates_enhanced,
)

# --------------------------------------------------------------------------- #
# clean_url
# --------------------------------------------------------------------------- #


def test_clean_url_strips_trailing_artifacts():
    assert clean_url("https://example.com/a.png')") == "https://example.com/a.png"
    assert clean_url('https://example.com/a.png",') == "https://example.com/a.png"
    assert clean_url("https://example.com/a.png)") == "https://example.com/a.png"
    assert clean_url("https://example.com/a.png.") == "https://example.com/a.png"


def test_clean_url_extracts_markdown_and_double_protocol():
    assert (
        clean_url("![](https://example.com/img.png)")
        == "https://example.com/img.png"
    )
    assert (
        clean_url("https://http://example.com/img.png")
        == "https://example.com/img.png"
    )
    assert (
        clean_url("http://https://example.com/img.png")
        == "https://example.com/img.png"
    )
    # percent-escaped markdown brackets get decoded when the result is sane
    assert (
        clean_url("https://example.com/%5Bimg%5D.png")
        == "https://example.com/[img].png"
    )


# --------------------------------------------------------------------------- #
# decode_camo
# --------------------------------------------------------------------------- #


def test_decode_camo_reverses_hex_origin():
    # hex of https://i.stack.imgur.com/AbC12.png
    origin = "https://i.stack.imgur.com/AbC12.png"
    hexed = origin.encode("utf-8").hex()
    pr = urlparse(f"https://camo.githubusercontent.com/abc123/{hexed}")
    assert decode_camo(pr) == origin


def test_decode_camo_rejects_malformed():
    assert decode_camo(urlparse("https://camo.githubusercontent.com/xyz")) is None
    assert decode_camo(urlparse("https://camo.githubusercontent.com/a/not-hex!")) is None
    # decoded value that is not http(s) is rejected
    hexed = "hello".encode("utf-8").hex()
    assert decode_camo(urlparse(f"https://camo.githubusercontent.com/a/{hexed}")) is None


# --------------------------------------------------------------------------- #
# badge_candidates
# --------------------------------------------------------------------------- #


def test_badge_hosts_count_is_22():
    assert len(BADGE_HOSTS) == 22


def test_badge_candidates_travis():
    cands = badge_candidates("https://travis-ci.org/octocat/Hello-World")
    joined = " ".join(cands)
    assert "https://travis-ci.org/octocat/Hello-World.svg" in cands
    assert any(
        "api.travis-ci.org/octocat/Hello-World.svg?branch=master" in c
        for c in cands
    )
    assert "travis-ci.org" in joined  # still rooted in the badge host


def test_badge_candidates_shields_and_fury():
    s = badge_candidates("https://img.shields.io/badge/build-passing-brightgreen")
    assert any("img.shields.io/badge/build-passing-brightgreen" in c for c in s)
    assert any("?style=flat" in c for c in s)
    f = badge_candidates("https://badge.fury.io/gh/octocat/Hello-World")
    assert "https://badge.fury.io/gh/octocat/Hello-World.svg" in f


# --------------------------------------------------------------------------- #
# special transforms
# --------------------------------------------------------------------------- #


def test_youtube_thumbnail_synthesis():
    cands = special_candidates_enhanced("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert cands[0] == "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"
    assert cands[1] == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
    short = special_candidates_enhanced("https://youtu.be/dQw4w9WgXcQ")
    assert "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg" in short


def test_plantuml_mermaid_github_dropbox_imgur():
    uml = special_candidates_enhanced(
        "https://www.plantuml.com/plantuml/uml/abc123=="
    )
    assert any("/plantuml/png/" in c for c in uml)
    assert any("/plantuml/svg/" in c for c in uml)

    blob = special_candidates_enhanced(
        "https://github.com/octocat/repo/blob/main/images/x.png"
    )
    assert (
        "https://raw.githubusercontent.com/octocat/repo/main/images/x.png" in blob
    )

    dropbox = special_candidates_enhanced("https://www.dropbox.com/s/abc/file.png?dl=0")
    assert any("dl=1" in c for c in dropbox)

    imgur = special_candidates_enhanced("https://imgur.com/gallery/90ynCaO")
    assert "https://i.imgur.com/90ynCaO.png" in imgur


# --------------------------------------------------------------------------- #
# page_candidates_enhanced (HTML scraping, offline)
# --------------------------------------------------------------------------- #

PAGE_HTML = """
<!DOCTYPE html>
<html><head>
  <meta property="og:image" content="https://cdn.example.com/og.png">
  <meta name="twitter:image:src" content="https://cdn.example.com/tw.png">
  <link rel="image_src" href="https://cdn.example.com/link.png">
  <link rel="icon" href="https://cdn.example.com/favicon.ico">
  <script type="application/ld+json">{"image": "https://cdn.example.com/ld.png"}</script>
</head><body>
  <img src="/img/a.png" srcset="/img/a.png 320w, /img/big.png 1600w"
       data-src="/img/lazy.png">
  <picture><source srcset="/pic/s.webp 800w"><img src="/pic/fallback.jpg"></picture>
  <a href="https://i.postimg.cc/xYz123/photo.png">photo</a>
  <a href="https://cdn.example.com/anchor.jpg">file</a>
</body></html>
"""


def test_page_candidates_enhanced_scoring_order():
    cands = page_candidates_enhanced("https://example.com/page", PAGE_HTML)
    assert cands[0] == "https://cdn.example.com/og.png"  # meta first (90)
    assert "https://cdn.example.com/ld.png" in cands       # JSON-LD
    assert "https://cdn.example.com/link.png" in cands     # link[rel=image_src]
    assert "https://i.postimg.cc/xYz123/photo.png" in cands  # download anchor
    assert "https://example.com/pic/s.webp" in cands       # srcset largest
    # favicon last (score 0)
    assert cands[-1] == "https://cdn.example.com/favicon.ico"