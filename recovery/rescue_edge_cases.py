#!/usr/bin/env python3
"""Stage 2 — external-edge resolver.

Port of ``sotoki-build/rescue_edge_cases_enhanced.py`` onto the recovery
library. Recovers hashes whose only candidate is a URL on an *external*
page (postimg.cc, github.com, badge hosts, youtube.com, plantuml.com, ...)
by synthesising a scored list of candidate image URLs per hash.

Pipeline position: after the XML-dump scans and the IA manifest, this module
turns "classified unmatched rows" into a candidate manifest (dry-run default)
and, with ``--fetch --no-dry-run``, downloads the best candidate per hash.

Candidate synthesis is deterministic and network-free:

- ``clean_url`` — strip trailing quotes/parens/punctuation, extract URLs from
  markdown, repair double-protocol artifacts.
- ``decode_camo`` — decode GitHub camo proxy hex paths back to origin URLs.
- ``badge_candidates`` — canonical SVG badge URLs for the 22 known CI/badge
  hosts (owner/repo extracted from the path).
- ``special_candidates_enhanced`` — YouTube maxresdefault/hqdefault
  thumbnails, PlantUML ``/uml/`` → ``/png/``, Mermaid mermaid.ink, GitHub
  blob → raw, Dropbox ``?dl=1``, Imgur page → direct i.imgur.com URL.
- ``page_candidates_enhanced`` — HTML scraping (JSON-LD, og:image,
  twitter:image, srcset largest descriptor, lazy attrs, background-image,
  picture/source, download anchors) with priority scores
  (meta=90, JSON-LD=85, anchors=60, srcset=50+, img src=40, favicon=0).

Fetching (only with ``--fetch`` + ``--no-dry-run``) goes through
``recovery.lib.images.download_image`` — H2 SSRF-safe URL validation, H3 TLS/
timeouts/byte caps, H4 per-host delay + backoff, H5 quota-stop, H6 magic-byte
+ HTML rejection, H7 WebP provenance. Every attempted ``(hash, url)`` is
recorded in the manifest (H8). On success the payload is written to
``--out-dir/<hash>``.

Dry-run (the default, H1) writes the candidate manifest and touches nothing
else: no sockets, no files in ``--out-dir``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from .lib.config import RecoveryConfig
    from .lib.images import download_image, fetch_page_text
    from .lib.manifest import ManifestWriter, default_tool_version, utc_now
except ImportError:  # allow `python3 recovery/rescue_edge_cases.py` too
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from recovery.lib.config import RecoveryConfig
    from recovery.lib.images import download_image, fetch_page_text
    from recovery.lib.manifest import ManifestWriter, default_tool_version, utc_now

VIDEO_EXTS = (".mp4", ".webm", ".mov")
IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff")

#: the 22 known CI/badge hosts the resolver tries canonical badge URLs for
BADGE_HOSTS = {
    "badge.fury.io",
    "img.shields.io",
    "travis-ci.org",
    "travis-ci.com",
    "codecov.io",
    "codeclimate.com",
    "david-dm.org",
    "api.codacy.com",
    "app.codacy.com",
    "coveralls.io",
    "scrutinizer-ci.com",
    "circleci.com",
    "ci.appveyor.com",
    "gemnasium.com",
    "inch-ci.org",
    "houndci.com",
    "semaphoreci.com",
    "bintray.com",
    "badges.gitter.im",
    "shippable.com",
    "requires.io",
    "readthedocs.org",
}

BADGE_REPO_PATTERNS = [
    # badge.fury.io/gh/<owner>/<repo>
    re.compile(r"badge\.fury\.io/(?:gh|js|rb|py)/([^/]+(?:/[^/]+)+)"),
    # travis-ci.org/<owner>/<repo> or travis-ci.com/<owner>/<repo>
    re.compile(r"travis-ci\.(?:org|com)/([^/]+(?:/[^/\s?]+)+)"),
    # codecov.io/gh/<owner>/<repo> or codecov.io/github/<owner>/<repo>
    re.compile(r"codecov\.io/(?:gh|github)/([^/]+(?:/[^/\s?]+)+)"),
    # codeclimate.com/github/<owner>/<repo>
    re.compile(r"codeclimate\.com/github/([^/]+(?:/[^/\s?]+)+)"),
    # david-dm.org/<owner>/<repo>
    re.compile(r"david-dm\.org/([^/]+(?:/[^/\s?]+)+)"),
    # coveralls.io/github/<owner>/<repo> or coveralls.io/repos/github/<owner>/<repo>
    re.compile(r"coveralls\.io/(?:repos/)?github/([^/]+(?:/[^/\s?]+)+)"),
    # img.shields.io/<badge path>
    re.compile(r"img\.shields\.io/(.+?)(?:\?|$)"),
    # readthedocs.org/projects/<name>/badge/
    re.compile(r"readthedocs\.org/projects/([^/]+)/badge/"),
]

#: download/anchor keywords that mark a link as image-bearing (H4-style hints)
_ANCHOR_HINT_RE = re.compile(
    r"download|i\.postimg\.cc|i\.ibb\.co|i\.imgur\.com|"
    r"raw\.githubusercontent\.com|dl\.dropbox|drive\.google\.com/uc",
    re.I,
)


# --------------------------------------------------------------------------- #
# URL cleanup (deterministic, network-free)
# --------------------------------------------------------------------------- #

def clean_url(url: str) -> str:
    """Strip garbage from malformed/extracted URLs."""
    url = (url or "").strip()
    # Strip trailing punctuation/quote artifacts
    while url and url[-1] in ("'", '"', ")", ",", ".", ";", ":"):
        url = url[:-1]
    # Decode markdown-embedded URLs like ![](https://example.com/img.png)
    m = re.search(r"(https?://[^\s\x00-\x1f<>\"']+)", url)
    if m:
        url = m.group(1)
        # Strip trailing parens again after extraction
        while url and url[-1] == ")":
            url = url[:-1]
    # Fix double-protocol artifacts
    url = re.sub(r"^https?://https?://", "https://", url)
    # Percent-decode common markdown escapes but leave valid percent-encoding
    if "%5B" in url or "%5D" in url or "%28" in url or "%29" in url:
        url_clean = (
            url.replace("%5B", "[").replace("%5D", "]")
            .replace("%28", "(").replace("%29", ")")
        )
        if url_clean.startswith("http"):
            url = url_clean
    return url


def is_unrecoverable_url(url: str) -> bool:
    """Quick skip for URLs no resolver can ever recover."""
    lowered = url.lower()
    if lowered.startswith(("http://...", "https://...")):
        return True
    if lowered.startswith("http://:none"):
        return True
    if lowered.startswith(("http://127.0.0.1", "http://localhost")):
        return True
    return False


# --------------------------------------------------------------------------- #
# GitHub camo decoder
# --------------------------------------------------------------------------- #

def decode_camo(pr: Any) -> Optional[str]:
    """Decode a GitHub camo proxy URL back to the origin image URL.

    Camo format: ``/<hex_hash>/<hex_encoded_origin_url>``. Returns None when
    the path is not a decodable camo form.
    """
    path = (pr.path or "").lstrip("/")
    if not path:
        return None
    parts = path.split("/", 1)
    if len(parts) != 2:
        return None
    try:
        origin_url = bytes.fromhex(parts[1]).decode("utf-8", errors="replace")
    except ValueError:
        return None
    if origin_url.startswith(("http://", "https://")):
        return origin_url
    return None


# --------------------------------------------------------------------------- #
# SVG badge transforms
# --------------------------------------------------------------------------- #

def badge_candidates(url: str) -> List[str]:
    """Canonical badge SVG/PNG URLs for known CI/badge hosts."""
    pr = _parse(url)
    host = (pr.netloc or "").lower()
    cands: List[str] = []

    # Try direct .svg/.png extension
    for ext in (".svg", ".png"):
        if any(h in host for h in BADGE_HOSTS):
            if not pr.path.endswith(ext):
                cands.append(f"{pr.scheme}://{host}{pr.path}{ext}")

    # Shields.io direct
    if "shields.io" in host:
        cands.append(url)
        if not pr.path.endswith(".svg"):
            cands.append(url + "?style=flat")

    # Repo-based badge URLs
    for pattern in BADGE_REPO_PATTERNS:
        m = pattern.search(url)
        if not m:
            continue
        repo = m.group(1)
        if "badge.fury.io" in host:
            cands.append(f"https://badge.fury.io/gh/{repo}.svg")
        if "travis-ci." in host:
            cands.append(f"https://api.travis-ci.org/{repo}.svg?branch=master")
        if "codecov.io" in host:
            cands.append(
                f"https://codecov.io/gh/{repo}/branch/master/graph/badge.svg"
            )
        if "codeclimate.com" in host:
            cands.append(
                f"https://api.codeclimate.com/v1/badges/github/{repo}/maintainability"
            )
        if "david-dm.org" in host:
            cands.append(f"https://david-dm.org/{repo}.svg")
        break

    return cands


# --------------------------------------------------------------------------- #
# HTML page candidate extraction (stdlib HTMLParser)
# --------------------------------------------------------------------------- #

class _PageParser(HTMLParser):
    """Collects scored image candidates from an HTML page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scored: List[Tuple[str, int]] = []
        self.favicons: List[str] = []
        self._in_jsonld = False
        self._jsonld_chunks: List[str] = []
        self._a_count = 0
        self._MAX_ANCHORS = 80

    # -- helpers ---------------------------------------------------------- #
    def _attrs(self, attrs: Sequence[Tuple[str, Optional[str]]]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for k, v in attrs:
            if v is not None:
                out[k.lower()] = v
        return out

    def _rel(self, a: Dict[str, str]) -> str:
        rel = a.get("rel", "")
        if isinstance(rel, list):
            rel = " ".join(rel)
        return rel.lower()

    def _add(self, value: str, score: int) -> None:
        value = (value or "").strip()
        if value:
            self.scored.append((value, score))

    # -- handlers --------------------------------------------------------- #
    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        a = self._attrs(attrs)
        low = tag.lower()
        if low == "meta":
            self._meta(a)
        elif low == "link":
            self._link(a)
        elif low == "img":
            self._img(a)
        elif low == "source":
            self._source(a)
        elif low == "a":
            self._anchor(a)
        elif low == "script" and (a.get("type", "") or "").lower() == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_jsonld:
            self._in_jsonld = False
            self._jsonld(self._jsonld_chunks)

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld_chunks.append(data)

    # -- tag logic -------------------------------------------------------- #
    def _meta(self, a: Dict[str, str]) -> None:
        prop = (a.get("property") or a.get("name") or "").lower()
        if prop in (
            "og:image",
            "og:image:secure_url",
            "twitter:image",
            "twitter:image:src",
            "thumbnail",
        ) or (a.get("itemprop") or "").lower() == "image":
            content = a.get("content", "")
            if content and content.startswith(("http://", "https://", "//")):
                self._add(content, 90)

    def _link(self, a: Dict[str, str]) -> None:
        rel = self._rel(a)
        href = a.get("href", "")
        if not href:
            return
        if "icon" in rel:
            self.favicons.append(href)
        elif "image_src" in rel and any(
            href.lower().endswith(ext) for ext in IMG_EXTS
        ):
            self._add(href, 80)

    def _img(self, a: Dict[str, str]) -> None:
        src = a.get("src")
        if src:
            self._add(src, 40)
        srcset = a.get("srcset")
        if srcset:
            best_src, best_size = self._largest_srcset(srcset)
            if best_src:
                self._add(best_src, 50 + min(best_size // 100, 30))
        for attr in ("data-src", "data-original", "data-lazy-src", "data-full",
                     "data-url", "data-image", "data-img"):
            val = a.get(attr)
            if val:
                self._add(val, 35)
        style = a.get("style", "")
        for bg in re.findall(
            r"background(?:-image)?\s*:\s*url\([\"']?([^\"'()]+)[\"']?\)",
            style, re.I,
        ):
            if bg.strip():
                self._add(bg.strip(), 30)

    def _source(self, a: Dict[str, str]) -> None:
        src = a.get("src") or a.get("srcset")
        if src:
            self._add(src.split(",")[0].strip().split(" ")[0], 55)

    def _anchor(self, a: Dict[str, str]) -> None:
        if self._a_count >= self._MAX_ANCHORS:
            return
        self._a_count += 1
        href = a.get("href", "")
        if not href:
            return
        low = href.lower()
        if _ANCHOR_HINT_RE.search(low):
            self._add(href, 60)
        elif low.endswith(IMG_EXTS + VIDEO_EXTS):
            self._add(href, 45)

    def _jsonld(self, chunks: List[str]) -> None:
        try:
            data = json.loads("".join(chunks))
        except (json.JSONDecodeError, TypeError):
            return
        keys = ("image", "thumbnailUrl", "contentUrl", "screenshot")
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in keys:
                val = item.get(key)
                if isinstance(val, str) and val.startswith("http"):
                    self._add(val, 85)
                elif isinstance(val, list):
                    for v in val:
                        if isinstance(v, str) and v.startswith("http"):
                            self._add(v, 80)

    @staticmethod
    def _largest_srcset(srcset: str) -> Tuple[Optional[str], int]:
        best_src: Optional[str] = None
        best_size = 0
        for part in srcset.split(","):
            part = part.strip()
            if not part:
                continue
            bits = part.rsplit(" ", 1)
            candidate_src = bits[0]
            size = 0
            if len(bits) == 2:
                try:
                    size = int(bits[1].rstrip("xw"))
                except ValueError:
                    size = 0
            if size > best_size:
                best_size = size
                best_src = candidate_src
        return best_src, best_size


def _parse(url: str) -> Any:
    from urllib.parse import urlparse

    return urlparse(url)


def page_candidates_enhanced(page_url: str, html_text: str) -> List[str]:
    """Extract image candidates from an HTML page, best first.

    Relative URLs are resolved against ``page_url``. Favicons (score 0) are
    appended last as a fallback. Deterministic, network-free.
    """
    from urllib.parse import urljoin

    parser = _PageParser()
    try:
        parser.feed(html_text or "")
        parser.close()
    except Exception:
        return []

    seen: set = set()
    result: List[str] = []
    for cand_url, _score in sorted(parser.scored, key=lambda x: -x[1]):
        if not cand_url.strip():
            continue
        resolved = urljoin(page_url, cand_url.strip())
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    for fav in parser.favicons:
        resolved = urljoin(page_url, fav)
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


# --------------------------------------------------------------------------- #
# special host transforms
# --------------------------------------------------------------------------- #

def special_candidates_enhanced(url: str) -> List[str]:
    """Alternative candidate URLs based on special host rules."""
    from urllib.parse import parse_qs, urlencode, urlunparse

    pr = _parse(url)
    host = (pr.netloc or "").lower()
    c: List[str] = []

    # YouTube thumbnails
    if "youtube.com" in host or "youtu.be" in host:
        vid = None
        if host.endswith("youtu.be"):
            vid = (pr.path or "").strip("/").split("/")[0]
        elif (pr.path or "").startswith("/shorts/"):
            parts = pr.path.split("/")
            vid = parts[2] if len(parts) >= 3 else None
        else:
            vid = parse_qs(pr.query).get("v", [None])[0]
        if vid:
            c += [
                f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg",
                f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                f"https://i.ytimg.com/vi/{vid}/sddefault.jpg",
                f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            ]

    # PlantUML
    if "plantuml.com" in host and "/plantuml/uml/" in (pr.path or ""):
        c += [
            url.replace("/plantuml/uml/", "/plantuml/png/"),
            url.replace("/plantuml/uml/", "/plantuml/svg/"),
        ]

    # Mermaid
    if "mermaid.live" in host and "#pako:" in url:
        code = url.split("#pako:", 1)[1]
        c += [
            f"https://mermaid.ink/img/pako:{code}",
            f"https://mermaid.ink/svg/pako:{code}",
        ]

    # GitHub blob -> raw
    if "github.com" in host and "/blob/" in (pr.path or ""):
        parts = (pr.path or "").strip("/").split("/")
        if len(parts) >= 5:
            owner, repo, _, branch = parts[:4]
            rest = "/".join(parts[4:])
            c.append(
                f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{rest}"
            )

    # Dropbox
    if "dropbox.com" in host:
        qs = parse_qs(pr.query)
        qs["dl"] = ["1"]
        c.append(urlunparse(pr._replace(query=urlencode(qs, doseq=True))))

    # OneDrive
    if "1drv.ms" in host:
        c.append(url + "?download=1")

    # Imgur page -> direct image
    if "imgur.com" in host and not host.startswith("i."):
        name = Path(pr.path).name
        if name:
            stem = Path(name).stem
            for ext in (".png", ".jpg", ".gif", ".mp4", ".webp"):
                c.append(f"https://i.imgur.com/{stem}{ext}")

    # GitHub camo
    if "camo.githubusercontent.com" in host:
        decoded = decode_camo(pr)
        if decoded:
            c.append(decoded)

    # Badge hosts
    if any(h in host for h in BADGE_HOSTS):
        c += badge_candidates(url)

    return c


# --------------------------------------------------------------------------- #
# classified-input loading
# --------------------------------------------------------------------------- #

def read_classified(path: Path) -> "dict[str, list[dict]]":
    """Group classify_missing TSV rows by hash, preserving URL order."""
    by_hash: "dict[str, list[dict]]" = defaultdict(list)
    with path.open("r", encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        for lineno, line in enumerate(fh, start=2):
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < len(header):
                sys.stderr.write(
                    f"warning: {path}:{lineno}: expected {len(header)} columns, "
                    f"got {len(parts)}; skipping\n"
                )
                continue
            row = dict(zip(header, parts))
            h = row.get("hash", "").lower()
            if not h:
                continue
            by_hash[h].append(row)
    return by_hash


def synthesized_candidates(rows: Sequence[dict]) -> List[Tuple[str, str]]:
    """Deterministic (url, source_class) candidate list in priority order.

    Special transforms first, then the cleaned original URL. Deduplicated by
    URL; rows without a usable http(s) URL contribute nothing.
    """
    out: List[Tuple[str, str]] = []
    seen: set = set()
    for row in rows:
        src = (row.get("source_url") or "").strip()
        cleaned = clean_url(src)
        if not cleaned.startswith(("http://", "https://")):
            continue
        if is_unrecoverable_url(cleaned):
            continue
        klass = row.get("source_class") or "other_http"
        for cand in special_candidates_enhanced(cleaned):
            if cand and cand not in seen:
                seen.add(cand)
                out.append((cand, klass))
        if cleaned not in seen:
            seen.add(cleaned)
            out.append((cleaned, klass))
    return out


# --------------------------------------------------------------------------- #
# per-hash fetch resolution (only with --fetch)
# --------------------------------------------------------------------------- #

def resolve_hash(
    hash: str,
    candidates: List[Tuple[str, str]],
    config: RecoveryConfig,
    out_dir: Path,
    manifest: Any,
    page_fetch_limit: int = 3,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Try candidates in order via download_image; record every attempt (H8).

    Returns ``(status, last_result)`` where status is ``ok`` or
    ``quota_exhausted`` (stop the whole run, H5) or ``failed``.
    """
    attempted: set = set()
    page_fetched: set = set()
    index = 0
    while index < len(candidates):
        url, source_class = candidates[index]
        index += 1
        if not url or url in attempted:
            continue
        attempted.add(url)
        result = download_image(url, out_dir / hash, config, hash=hash)
        manifest.add(
            hash=hash,
            source_url=url,
            source_class=source_class,
            status=result.get("status", "error"),
            content_sha256=result.get("content_sha256", ""),
            derived_sha256=result.get("derived_sha256", ""),
            mime=result.get("mime", ""),
            bytes=result.get("bytes") or None,
        )
        if result.get("status") == "ok":
            return "ok", result
        if result.get("status") == "quota_exhausted":
            return "quota_exhausted", result
        # invalid_content may mean the URL is an HTML page: scrape it for
        # better candidates (bounded to page_fetch_limit pages per hash).
        if (
            result.get("status") == "invalid_content"
            and url not in page_fetched
            and len(page_fetched) < page_fetch_limit
        ):
            page_fetched.add(url)
            text_status, text, _reason = fetch_page_text(url, config)
            if text_status == "ok":
                for pc in page_candidates_enhanced(url, text):
                    if pc not in attempted:
                        candidates.append((pc, source_class + "_page"))
    return "failed", None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def run(
    classified_path: Path,
    out_manifest: Path,
    out_dir: Path,
    config: RecoveryConfig,
    limit: int = 0,
) -> Dict[str, Any]:
    """Build the edge candidate manifest; fetch when ``config.fetch_ok``."""
    by_hash = read_classified(classified_path)
    out_manifest.parent.mkdir(parents=True, exist_ok=True)
    if config.fetch_ok:
        out_dir.mkdir(parents=True, exist_ok=True)

    stats = {
        "hashes": len(by_hash),
        "candidate_rows": 0,
        "recovered": 0,
        "failed": 0,
        "quota_exhausted": False,
        "processed": 0,
    }
    tool_version = default_tool_version()

    with ManifestWriter(out_manifest) as manifest:
        for h in sorted(by_hash):
            if limit and stats["processed"] >= limit:
                break
            stats["processed"] += 1
            candidates = synthesized_candidates(by_hash[h])
            if config.fetch_ok:
                status, _result = resolve_hash(h, candidates, config, out_dir, manifest)
                if status == "ok":
                    stats["recovered"] += 1
                elif status == "quota_exhausted":
                    stats["failed"] += 1
                    stats["quota_exhausted"] = True
                    break  # H5: stop, operator intervention required
                else:
                    stats["failed"] += 1
            else:
                for url, klass in candidates:
                    manifest.add(
                        hash=h,
                        source_url=url,
                        source_class=klass,
                        status="candidate",
                        timestamp=utc_now(),
                        tool_version=tool_version,
                    )
                    stats["candidate_rows"] += 1

    return stats


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="External-edge image resolver (scored candidate system)"
    )
    ap.add_argument("--classified", required=True, help="classify_missing TSV")
    ap.add_argument("--out-manifest", required=True, help="output manifest TSV")
    ap.add_argument("--out-dir", required=True, help="recovered images dir")
    ap.add_argument("--config", default=None, help="optional RecoveryConfig JSON")
    ap.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="dry-run (default): candidate manifest only, no sockets",
    )
    ap.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="enable live mode (with --fetch)",
    )
    ap.add_argument(
        "--fetch",
        action="store_true",
        default=False,
        help="enable network downloads (requires --no-dry-run)",
    )
    ap.add_argument("--delay", type=float, default=0.5, help="per-host delay s")
    ap.add_argument("--limit", type=int, default=0, help="max hashes to process")
    args = ap.parse_args(argv)

    config = RecoveryConfig.from_args(args)
    stats = run(
        Path(args.classified),
        Path(args.out_manifest),
        Path(args.out_dir),
        config,
        limit=args.limit,
    )
    mode = "DRY-RUN" if config.dry_run else "LIVE"
    sys.stderr.write(
        f"DONE [{mode}] hashes={stats['hashes']} processed={stats['processed']} "
        f"candidate_rows={stats['candidate_rows']} recovered={stats['recovered']} "
        f"failed={stats['failed']} quota_exhausted={stats['quota_exhausted']} "
        f"-> {args.out_manifest}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())