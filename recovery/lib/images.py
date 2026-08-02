"""Image bytes utilities + hardened downloader.

Hardening implemented here (see recovery/README.md for the full H1-H10 list):

- H2: HTTP(S) only, credentials rejected, every hop (initial URL AND each
  redirect target) resolved and validated to a *global* IP before connect,
  connected peer re-validated after connect.
- H3: TLS verification always on (never disabled), max 5 redirects, 10s
  connect + 30s read timeouts, 25 MB streaming byte cap, temp-file + atomic
  rename promotion.
- H4: descriptive User-Agent, per-host throttle (default 0.5 s), bounded
  retries with exponential backoff (base 2 s, max 60 s, max 5), Retry-After
  honored, resumable checkpoint store.
- H5: on quota exhaustion (429/403 after retries) the download stops and
  checkpoints — no rotation, no evasion.
- H6: Content-Type is advisory only: magic-byte sniffing + PIL decode
  verification; HTML/XML/SVG rejected; byte caps + pixel/frame limits.
- H7: WebP conversion preserves original SHA-256/provenance; the converted
  asset's hash is recorded separately (derived_sha256).

Network execution is gated behind ``config.fetch_ok`` (H1): without an
explicit ``--fetch`` plus a non-dry-run config, ``download_image`` returns a
``dry-run`` status and never opens a socket.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .config import CONNECT_TIMEOUT, RecoveryConfig

# --------------------------------------------------------------------------- #
# constants (H3/H6)
# --------------------------------------------------------------------------- #

MAX_PIXELS = 100_000_000  # 100 MP
MAX_DIMENSION = 16384
MAX_REDIRECT_DEFAULT = 5
MAX_RETRIES_DEFAULT = 5
BACKOFF_MAX = 60.0

#: magic-byte prefixes we recognise
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_GIF_MAGICS = (b"GIF87a", b"GIF89a")
_ICO_MAGIC = b"\x00\x00\x01\x00"

_HTML_SNIPPET_TOKENS = (b"<!doctype", b"<html", b"<?xml", b"<svg")

_EXT_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/x-icon": ".ico",
}


class ImageDownloadError(Exception):
    """Base class for download/validation failures."""


class DownloadValidationError(ImageDownloadError):
    """A URL/hop failed SSRF/global-IP validation (H2)."""


# --------------------------------------------------------------------------- #
# hashing + sniffing
# --------------------------------------------------------------------------- #

def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sniff_bytes(data: bytes) -> str:
    """MIME from magic bytes. Content-Type headers are advisory (H6)."""
    d = data.lstrip()
    if d.startswith(_PNG_MAGIC):
        return "image/png"
    if d.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    if d.startswith(_GIF_MAGICS):
        return "image/gif"
    if d.startswith(b"RIFF") and len(d) >= 12 and d[8:12] == b"WEBP":
        return "image/webp"
    if d.startswith(b"BM"):
        return "image/bmp"
    if d.startswith(_ICO_MAGIC):
        return "image/x-icon"
    if d.startswith((b"<?xml", b"<svg", b"<SVG")):
        return "image/svg+xml"
    return "unknown"


def _decode_ok(data: bytes) -> bool:
    """PIL open + verify + pixel-limit check (H6)."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img.verify()
        width, height = img.size
        if width <= 0 or height <= 0:
            return False
        if width > MAX_DIMENSION or height > MAX_DIMENSION:
            return False
        if width * height > MAX_PIXELS:
            return False
        return True
    except ImportError:
        # PIL unavailable: magic-byte check only (documented fallback).
        return True
    except Exception:
        return False


def is_valid_image(data: bytes) -> bool:
    """True when data sniffs as a known raster image, is not HTML/XML/SVG,
    and decodes within pixel limits."""
    if not data or len(data) < 4:
        return False
    mime = sniff_bytes(data)
    if mime in ("unknown", "image/svg+xml"):
        return False
    head = data[:4096].lower()
    if any(token in head for token in _HTML_SNIPPET_TOKENS):
        return False
    return _decode_ok(data)


# --------------------------------------------------------------------------- #
# placeholder detection (H10)
# --------------------------------------------------------------------------- #

def is_placeholder(path: Pathish, spec: Dict[str, Any]) -> bool:
    """True only when size AND content SHA-256 both match the spec.

    ``size == placeholder_bytes`` is a PREFILTER only; when the spec does not
    carry a recorded ``sha256`` yet, a file can NOT be confirmed as a
    placeholder and this returns False (never classify by size alone, H10).
    """
    p = Path(path)
    try:
        if not p.is_file():
            return False
        if p.stat().st_size != int(spec.get("size_bytes", 0)):
            return False
        expected = spec.get("sha256")
        if not expected:
            return False  # unversioned spec: size alone is not proof (H10)
        actual = sha256_of(p.read_bytes())
        return actual.lower() == str(expected).lower()
    except (OSError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# URL validation (H2)
# --------------------------------------------------------------------------- #

def _is_global_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if not ip.is_global:
        return False
    # belt-and-braces: is_global may be conservative on some builds
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        return False
    if ip.is_unspecified or ip.is_private:
        return False
    return True


def _resolve_host_ips(host: str, resolver: Optional[Callable[[str], Sequence[str]]] = None) -> List[str]:
    if resolver is not None:
        ips = list(resolver(host))
    else:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        ips = []
        for info in infos:
            addr = info[4][0]
            if addr not in ips:
                ips.append(addr)
    return ips


def _require_global_resolution(host: str, resolver: Optional[Callable[[str], Sequence[str]]] = None) -> List[str]:
    """Resolve and require every address to be global; raise otherwise (H2)."""
    try:
        ips = _resolve_host_ips(host, resolver=resolver)
    except OSError as exc:
        raise DownloadValidationError(f"could not resolve {host!r}: {exc}")
    if not ips:
        raise DownloadValidationError(f"no addresses resolved for {host!r}")
    for addr in ips:
        if not _is_global_ip(addr):
            raise DownloadValidationError(
                f"{host!r} resolves to non-global address {addr!r}; refusing"
            )
    return ips


def validate_url(
    url: str,
    resolver: Optional[Callable[[str], Sequence[str]]] = None,
) -> bool:
    """True when ``url`` is HTTP(S), credential-free, and resolves only to
    global IPs. ``resolver`` is injectable for offline tests."""
    return bool(validate_url_detail(url, resolver=resolver)[0])


def validate_url_detail(
    url: str,
    resolver: Optional[Callable[[str], Sequence[str]]] = None,
) -> Tuple[bool, str]:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        return False, f"unparsable url: {exc}"
    if parsed.scheme not in ("http", "https"):
        return False, f"scheme {parsed.scheme!r} not http(s)"
    if parsed.username or parsed.password:
        return False, "url contains credentials; refusing"
    host = parsed.hostname
    if not host:
        return False, "url has no host"
    # IP literal: validate without DNS (offline-safe).
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if _is_global_ip(host):
            return True, ""
        return False, f"{host!r} is not a global IP"
    try:
        _require_global_resolution(host, resolver=resolver)
    except DownloadValidationError as exc:
        return False, str(exc)
    return True, ""


# --------------------------------------------------------------------------- #
# connection hardening (H2/H3)
# --------------------------------------------------------------------------- #

def _make_tls_context() -> ssl.SSLContext:
    """Always-verifying TLS context (H3). Never disable verification."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    try:
        ctx.verify_mode = ssl.CERT_REQUIRED
    except ValueError:
        pass  # check_hostname=True already forces CERT_REQUIRED
    return ctx


class _SafeHTTPConnection(http.client.HTTPConnection):
    """Validate resolved addresses before connect, peer after connect (H2)."""

    _read_timeout: Optional[float] = 30.0

    def connect(self) -> None:
        _require_global_resolution(self.host)
        super().connect()
        peer = self.sock.getpeername()[0]
        if not _is_global_ip(peer):
            raise DownloadValidationError(
                f"connected peer {peer!r} is not a global address"
            )
        if self._read_timeout:
            self.sock.settimeout(self._read_timeout)


class _SafeHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS variant; TLS verification always on (H3)."""

    _read_timeout: Optional[float] = 30.0

    def __init__(
        self,
        host: str,
        port: Optional[int] = None,
        *,
        timeout: Optional[float] = None,
        context: Optional[ssl.SSLContext] = None,
        check_hostname: Optional[bool] = None,
        source_address: Optional[Tuple[str, int]] = None,
        blocksize: int = 8192,
    ) -> None:
        ctx = context if context is not None else _make_tls_context()
        ctx.check_hostname = True
        try:
            ctx.verify_mode = ssl.CERT_REQUIRED
        except ValueError:
            pass
        super().__init__(
            host,
            port,
            timeout=timeout,
            source_address=source_address,
            context=ctx,
            check_hostname=check_hostname,
            blocksize=blocksize,
        )

    def connect(self) -> None:
        _require_global_resolution(self.host)
        super().connect()
        peer = self.sock.getpeername()[0]
        if not _is_global_ip(peer):
            raise DownloadValidationError(
                f"connected peer {peer!r} is not a global address"
            )
        if self._read_timeout:
            self.sock.settimeout(self._read_timeout)


class _ValidatedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, config: RecoveryConfig, debuglevel: int = 0) -> None:
        super().__init__(debuglevel=debuglevel)
        self._config = config

    def http_open(self, req: urllib.request.Request) -> Any:
        conn = _SafeHTTPConnection(req.host, req.port, timeout=CONNECT_TIMEOUT)
        conn._read_timeout = self._config.timeout
        return _perform_request(conn, req, self.debuglevel)


class _ValidatedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, config: RecoveryConfig, debuglevel: int = 0) -> None:
        super().__init__(debuglevel=debuglevel)
        self._config = config

    def https_open(self, req: urllib.request.Request) -> Any:
        conn = _SafeHTTPSConnection(
            req.host, req.port, timeout=CONNECT_TIMEOUT, context=_make_tls_context()
        )
        conn._read_timeout = self._config.timeout
        return _perform_request(conn, req, self.debuglevel)


class _BoundedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirects bounded per H3 (max 5); each hop re-validated by the
    connection handlers above (H2)."""

    def __init__(self, max_redirects: int = MAX_REDIRECT_DEFAULT) -> None:
        super().__init__()
        self.max_redirections = int(max_redirects)


def _perform_request(conn: Any, req: urllib.request.Request, debuglevel: int) -> Any:
    """Mirror of AbstractHTTPHandler.do_open body with our connection."""
    conn.set_debuglevel(debuglevel)
    headers = dict(req.unredirected_hdrs)
    headers.update({k: v for k, v in req.headers.items() if k not in headers})
    headers["Connection"] = "close"
    headers = {name.title(): val for name, val in headers.items()}
    body = req.data
    if req.get_method() == "POST" and body is None:
        body = b""
    conn.request(
        req.get_method(),
        req.selector,
        body,
        headers,
        encode_chunked=req.has_header("Transfer-encoding"),
    )
    response = conn.getresponse()
    response.url = req.full_url
    response.msg = response.reason
    return response


# --------------------------------------------------------------------------- #
# throttling + checkpoints (H4/H5)
# --------------------------------------------------------------------------- #

class PerHostThrottle:
    """Min-interval per host; safe for concurrent use."""

    def __init__(self, min_interval: float = 0.5) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self._last: Dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            last = self._last.get(host, 0.0)
            gap = self.min_interval - (now - last)
            if gap > 0:
                time.sleep(gap)
            self._last[host] = time.monotonic()


class CheckpointStore:
    """Resumable checkpoint: records completed hashes so an interrupted run
    can resume (H4). Format: ``hash<TAB>status<TAB>url<TAB>iso-timestamp``."""

    def __init__(self, path: Pathish) -> None:
        self.path = Path(path)
        self._done: Dict[str, str] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split("\t")
                if parts and parts[0]:
                    self._done[parts[0]] = parts[1] if len(parts) > 1 else "done"

    def contains(self, hash: str) -> bool:
        return hash in self._done

    def status_of(self, hash: str) -> str:
        return self._done.get(hash, "")

    def mark(self, hash: str, status: str, url: str = "") -> None:
        self._done[hash] = status
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(f"{hash}\t{status}\t{url}\t{ts}\n")
            fh.flush()
            os.fsync(fh.fileno())


# --------------------------------------------------------------------------- #
# retry / backoff helpers (H4/H5)
# --------------------------------------------------------------------------- #

def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    delta = (when - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delta)


def _backoff_delay(attempt: int, config: RecoveryConfig) -> float:
    return min(BACKOFF_MAX, config.backoff_base * (2 ** attempt))


def _parse_host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return ""


# --------------------------------------------------------------------------- #
# the downloader
# --------------------------------------------------------------------------- #

def _make_opener(config: RecoveryConfig) -> urllib.request.OpenerDirector:
    handlers = [
        urllib.request.ProxyHandler({}),  # always direct; SSRF checks are ours
        _ValidatedHTTPHandler(config),
        _ValidatedHTTPSHandler(config),
        _BoundedRedirectHandler(config.max_redirects),
    ]
    return urllib.request.build_opener(*handlers)


def _download_once(
    url: str,
    config: RecoveryConfig,
    opener: urllib.request.OpenerDirector,
) -> Tuple[str, bytes, str, Optional[str]]:
    """One fetch attempt. Returns (status, data, reason, retry_after).

    statuses: 'ok', 'too_large', 'invalid_content', 'http_error',
    'validation_error', 'network_error'. ``retry_after`` is the raw
    Retry-After header when the server sent one (H4).
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": config.user_agent,
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,*/*;q=0.8",
        },
    )
    try:
        response = opener.open(request)
    except urllib.error.HTTPError as exc:
        return "http_error", b"", f"HTTP {exc.code} {exc.reason}", exc.headers.get("Retry-After") if exc.headers else None
    except DownloadValidationError as exc:
        return "validation_error", b"", str(exc), None
    except urllib.error.URLError as exc:
        return "network_error", b"", str(exc.reason or exc), None
    except (OSError, ssl.SSLError) as exc:
        return "network_error", b"", f"{type(exc).__name__}: {exc}", None
    try:
        data = response.read(config.max_bytes + 1)
    except (OSError, ssl.SSLError, http.client.IncompleteRead) as exc:
        return "network_error", b"", f"read failed: {exc}", None
    finally:
        try:
            response.close()
        except Exception:
            pass
    if len(data) > config.max_bytes:
        return "too_large", b"", (
            f"payload {len(data)} bytes exceeds max_bytes={config.max_bytes} (H3/H6)"
        ), None
    if not is_valid_image(data):
        return "invalid_content", b"", "magic/decode validation failed (H6)", None
    return "ok", data, "", None


def download_image(
    url: str,
    dest_path: Pathish,
    config: RecoveryConfig,
    *,
    checkpoint: Optional[CheckpointStore] = None,
    throttle: Optional[PerHostThrottle] = None,
    hash: Optional[str] = None,
) -> Dict[str, Any]:
    """Download one image with full H2-H7 hardening.

    Returns a status dict; never raises for expected failures. The payload is
    written to ``dest_path`` only on success, via temp-file + atomic rename
    (H3). In dry-run mode (no ``config.fetch_ok``) it returns
    ``status='dry-run'`` immediately without opening sockets or writing
    payloads (H1).
    """
    dest = Path(dest_path)
    result: Dict[str, Any] = {
        "url": url,
        "status": "dry-run",
        "reason": "",
        "mime": "",
        "bytes": 0,
        "content_sha256": "",
        "derived_sha256": "",
    }
    if not config.fetch_ok:
        result["reason"] = (
            "network disabled: pass --fetch and disable dry-run to download"
        )
        return result

    ok, reason = validate_url_detail(url)
    if not ok:
        result["status"] = "validation_error"
        result["reason"] = reason
        return result

    if checkpoint is not None and hash and checkpoint.contains(hash):
        result["status"] = "already_done"
        result["reason"] = f"checkpoint: {checkpoint.status_of(hash)}"
        return result

    throttle = throttle or PerHostThrottle(config.delay)
    host = _parse_host(url)
    opener = _make_opener(config)

    status = ""
    data = b""
    reason = ""
    for attempt in range(config.max_retries + 1):
        throttle.wait(host or url)
        status, data, reason, retry_after = _download_once(url, config, opener)
        if status == "ok":
            break
        if status in ("validation_error", "too_large", "invalid_content"):
            break  # deterministic; retrying cannot help
        if status == "http_error":
            code = int(reason.split()[1])
            if code in (429, 403):
                # H5: quota-class codes are retried, and only after retries
                # are exhausted do we STOP and checkpoint for the operator.
                if attempt >= config.max_retries:
                    status = "quota_exhausted"
                    reason = (
                        "quota exhausted after retries; operator intervention "
                        "required (H5)"
                    )
                    break
                wait = _parse_retry_after(retry_after) or _backoff_delay(attempt, config)
                time.sleep(min(wait, BACKOFF_MAX))
                continue
            if code in (408, 425) or 500 <= code <= 599:
                if attempt >= config.max_retries:
                    break
                wait = _backoff_delay(attempt, config)
                time.sleep(min(wait, BACKOFF_MAX))
                continue
            break  # other 4xx: permanent, do not retry
        # network_error
        if attempt >= config.max_retries:
            break
        wait = _backoff_delay(attempt, config)
        time.sleep(min(wait, BACKOFF_MAX))

    if status == "ok":
        content_sha = sha256_of(data)
        result.update(
            {
                "status": "ok",
                "reason": "",
                "mime": sniff_bytes(data),
                "bytes": len(data),
                "content_sha256": content_sha,
            }
        )
        # H7: convert to WebP preserving provenance; derived hash recorded
        # separately. Original bytes remain the canonical payload on disk.
        derived, derived_sha = convert_to_webp(data, quality=82)
        result["derived_sha256"] = derived_sha
        if derived is not data and derived_sha and len(derived) > 0:
            result["mime"] = "image/webp"
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=dest.name + ".", suffix=".tmp", dir=str(dest.parent)
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, dest)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        if checkpoint is not None and hash:
            checkpoint.mark(hash, "ok", url)
    else:
        result["status"] = status
        result["reason"] = reason
        if checkpoint is not None and hash:
            checkpoint.mark(hash, result["status"], url)
    return result


# --------------------------------------------------------------------------- #
# page-text fetch for HTML scraping (edge resolver)
# --------------------------------------------------------------------------- #

_PAGE_SNIPPET_TOKENS = (b"<html", b"<head", b"<body", b"<meta", b"<!doctype", b"<script", b"<div", b"<img", b"<a ")


def fetch_page_text(
    url: str,
    config: RecoveryConfig,
    max_bytes: int = 4 * 1024 * 1024,
    throttle: Optional[PerHostThrottle] = None,
) -> Tuple[str, str, str]:
    """Fetch an HTML page body through the hardened opener for scraping.

    This is a *page* fetch, never an image download: the returned text is
    only ever parsed for candidate image URLs and is never written to the
    stage (H6 rejection of HTML payloads is unaffected). All H2/H3/H4
    protections apply (SSRF-validated hops, TLS always on, timeouts, byte
    cap). Returns ``(status, text, reason)``; ``status`` is ``ok`` only for
    a bounded, decodable text body. In dry-run mode (no ``config.fetch_ok``)
    it returns ``status='dry-run'`` without opening sockets (H1).
    """
    if not config.fetch_ok:
        return "dry-run", "", (
            "network disabled: pass --fetch and disable dry-run to fetch pages"
        )
    ok, reason = validate_url_detail(url)
    if not ok:
        return "validation_error", "", reason
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": config.user_agent,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        },
    )
    opener = _make_opener(config)
    (throttle or PerHostThrottle(config.delay)).wait(_parse_host(url) or url)
    try:
        response = opener.open(request)
    except urllib.error.HTTPError as exc:
        return "http_error", "", f"HTTP {exc.code} {exc.reason}"
    except DownloadValidationError as exc:
        return "validation_error", "", str(exc)
    except urllib.error.URLError as exc:
        return "network_error", "", str(exc.reason or exc)
    except (OSError, ssl.SSLError) as exc:
        return "network_error", "", f"{type(exc).__name__}: {exc}"
    try:
        data = response.read(max_bytes + 1)
    except (OSError, ssl.SSLError, http.client.IncompleteRead) as exc:
        return "network_error", "", f"read failed: {exc}"
    finally:
        try:
            response.close()
        except Exception:
            pass
    if len(data) > max_bytes:
        return "too_large", "", f"page payload exceeds {max_bytes} bytes (H3)"
    if not any(tok in data[:4096].lower() for tok in _PAGE_SNIPPET_TOKENS):
        return "not_html", "", "payload does not look like an HTML page"
    return "ok", data.decode("utf-8", errors="replace"), ""


# --------------------------------------------------------------------------- #
# WebP conversion (H7)
# --------------------------------------------------------------------------- #

def convert_to_webp(data: bytes, quality: int = 82) -> Tuple[bytes, str]:
    """Convert image bytes to WebP; returns (bytes, derived_sha256).

    Preference: cwebp subprocess, then PIL; if neither is available the
    original bytes are returned unchanged with the ORIGINAL sha (the caller
    keeps ``content_sha256`` = original sha, H7).
    """
    if not data:
        return data, sha256_of(data)
    original_sha = sha256_of(data)
    mime = sniff_bytes(data)
    if mime in ("unknown", "image/svg+xml"):
        return data, original_sha
    ext = _EXT_BY_MIME.get(mime, ".bin")

    cwebp = shutil.which("cwebp")
    if cwebp:
        try:
            with tempfile.TemporaryDirectory() as td:
                src = Path(td) / ("in" + ext)
                dst = Path(td) / "out.webp"
                src.write_bytes(data)
                subprocess.run(
                    [cwebp, "-quiet", "-q", str(quality), str(src), "-o", str(dst)],
                    timeout=60,
                    capture_output=True,
                )
                if dst.exists() and dst.stat().st_size > 0:
                    out = dst.read_bytes()
                    if out:
                        return out, sha256_of(out)
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        width, height = img.size
        if width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > MAX_PIXELS:
            return data, original_sha
        if img.mode not in ("RGB", "RGBA"):
            if "A" in img.getbands():
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=quality)
        out = buf.getvalue()
        if out:
            return out, sha256_of(out)
    except ImportError:
        pass  # no PIL: return original
    except Exception:
        pass
    return data, original_sha


# --------------------------------------------------------------------------- #
# tiny convenience for tests
# --------------------------------------------------------------------------- #

def tiny_png_bytes(width: int = 4, height: int = 4) -> bytes:
    """Deterministic tiny PNG (test fixture helper; stdlib-only)."""
    import struct
    import zlib

    def chunk(tag: bytes, payload: bytes) -> bytes:
        c = tag + payload
        return struct.pack(">I", len(payload)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x20\x40\x60" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )