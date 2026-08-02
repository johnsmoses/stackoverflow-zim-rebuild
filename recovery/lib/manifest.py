"""Manifest writer/reader shared by every recovery stage.

Every manifest is either TSV or JSONL with the same schema:

``schema_version, hash, source_url, source_class, status, content_sha256,
derived_sha256, mime, bytes, timestamp, tool_version``

- ``content_sha256``: SHA-256 of the ORIGINAL downloaded bytes (H7).
- ``derived_sha256``: SHA-256 of the WebP-converted bytes (H7); empty when no
  conversion was performed.
- ``source_url``: where the record came from (download URL / IA URL / page).
- ``source_class``: provenance bucket (``stage``, ``xml_dump``,
  ``posthistory_xml``, ``ia_stack_imgur``, ...).
- ``status``: attempt status (``candidate``, ``recovered``, ``dry-run``,
  ``quota_exhausted``, ``error``, ...).

Deduplication (H8) is per ``(hash, source_url)`` pair — a hash that appears
under multiple sources yields multiple rows. Deduplication by hash alone is
never performed.

Writes are append-only in semantics and atomic on disk: the writer loads any
existing rows, accepts new rows, and on ``close()`` promotes a fully-written
temp file over the destination with an atomic rename.
"""

from __future__ import annotations

import datetime
import importlib.metadata
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import recovery

SCHEMA_VERSION = "1"
TSV_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "hash",
    "source_url",
    "source_class",
    "status",
    "content_sha256",
    "derived_sha256",
    "mime",
    "bytes",
    "timestamp",
    "tool_version",
)


def default_tool_version() -> str:
    """tool_version: prefer the installed sotoki version, else recovery's."""
    try:
        return "sotoki-" + importlib.metadata.version("sotoki")
    except Exception:
        return f"recovery-{recovery.__version__}"


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _tsv_escape(value: Any) -> str:
    if value is None:
        return ""
    s = str(value)
    return s.replace("\t", "%09").replace("\n", "%0A").replace("\r", "%0D")


def _normalise_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Fill defaults for every schema field (missing -> empty)."""
    out: Dict[str, Any] = {}
    for field in TSV_FIELDS:
        out[field] = row.get(field, "")
    out["schema_version"] = SCHEMA_VERSION
    return out


def detect_format(path: Pathish) -> str:
    """Guess the manifest format from the file extension."""
    suffix = Path(path).suffix.lower()
    if suffix == ".jsonl":
        return "jsonl"
    if suffix == ".tsv":
        return "tsv"
    return "tsv"


class ManifestWriter:
    """Append-only, atomic, (hash, source_url)-deduplicating manifest writer.

    Example::

        with ManifestWriter("recovered.tsv") as w:
            w.add(hash=h, source_url=url, source_class="xml_dump",
                  status="candidate")
    """

    def __init__(
        self,
        path: Pathish,
        fmt: Optional[str] = None,
        tool_version: Optional[str] = None,
        overwrite: bool = False,
    ) -> None:
        self.path = Path(path)
        self.fmt = (fmt or detect_format(self.path)).lower()
        if self.fmt not in ("tsv", "jsonl"):
            raise ValueError(f"unsupported manifest format: {self.fmt!r}")
        self.tool_version = tool_version or default_tool_version()
        self._rows: List[Dict[str, Any]] = []
        self._seen: set = set()
        self.duplicates = 0
        self._closed = False
        if self.path.exists() and not overwrite:
            self._load_existing()

    # ------------------------------------------------------------------ #
    def _load_existing(self) -> None:
        reader = ManifestReader(self.path, fmt=self.fmt)
        for row in reader.rows():
            self._rows.append(row)
            self._seen.add((row.get("hash", ""), row.get("source_url", "")))

    def add(
        self,
        hash: str,
        source_url: str = "",
        source_class: str = "",
        status: str = "",
        content_sha256: str = "",
        derived_sha256: str = "",
        mime: str = "",
        bytes: Optional[int] = None,
        timestamp: Optional[str] = None,
        tool_version: Optional[str] = None,
    ) -> bool:
        """Record one row. Returns True if written, False if duplicate
        ``(hash, source_url)`` pair (H8)."""
        if self._closed:
            raise RuntimeError("ManifestWriter already closed")
        key = (str(hash), str(source_url))
        if key in self._seen:
            self.duplicates += 1
            return False
        self._seen.add(key)
        row = _normalise_row(
            {
                "hash": hash,
                "source_url": source_url,
                "source_class": source_class,
                "status": status,
                "content_sha256": content_sha256 or "",
                "derived_sha256": derived_sha256 or "",
                "mime": mime or "",
                "bytes": bytes,
                "timestamp": timestamp or utc_now(),
                "tool_version": tool_version or self.tool_version,
            }
        )
        self._rows.append(row)
        return True

    def add_row(self, row: Dict[str, Any]) -> bool:
        """Add a row from an arbitrary dict (schema fields only are kept)."""
        return self.add(
            hash=row.get("hash", ""),
            source_url=row.get("source_url", ""),
            source_class=row.get("source_class", ""),
            status=row.get("status", ""),
            content_sha256=row.get("content_sha256", ""),
            derived_sha256=row.get("derived_sha256", ""),
            mime=row.get("mime", ""),
            bytes=row.get("bytes"),
            timestamp=row.get("timestamp"),
            tool_version=row.get("tool_version"),
        )

    @property
    def row_count(self) -> int:
        return len(self._rows)

    # ------------------------------------------------------------------ #
    def _write_tsv(self, fh: Any) -> None:
        fh.write("\t".join(TSV_FIELDS) + "\n")
        for row in self._rows:
            fh.write("\t".join(_tsv_escape(row.get(f, "")) for f in TSV_FIELDS) + "\n")

    def _write_jsonl(self, fh: Any) -> None:
        for row in self._rows:
            obj = {f: (row.get(f, "") if row.get(f, "") is not None else "") for f in TSV_FIELDS}
            fh.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")

    def close(self) -> None:
        """Atomically promote the manifest (temp file + rename)."""
        if self._closed:
            return
        self._closed = True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                if self.fmt == "tsv":
                    self._write_tsv(fh)
                else:
                    self._write_jsonl(fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def discard(self) -> None:
        """Close without promoting (abandon the pending temp state)."""
        self._closed = True

    def __enter__(self) -> "ManifestWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        if exc[0] is None:
            self.close()
        else:
            self.discard()


class ManifestReader:
    """Read TSV/JSONL manifests of the recovery schema."""

    def __init__(self, path: Pathish, fmt: Optional[str] = None) -> None:
        self.path = Path(path)
        self.fmt = (fmt or detect_format(self.path)).lower()
        if not self.path.exists():
            raise FileNotFoundError(self.path)

    def _open_text(self) -> Any:
        return self.path.open("r", encoding="utf-8", newline="")

    def rows(self) -> Iterator[Dict[str, Any]]:
        """Yield schema dicts, one per manifest record."""
        if self.fmt == "tsv":
            yield from self._rows_tsv()
        else:
            yield from self._rows_jsonl()

    def _rows_tsv(self) -> Iterator[Dict[str, Any]]:
        with self._open_text() as fh:
            header = fh.readline().rstrip("\n")
            if not header.startswith("schema_version"):
                raise ValueError(
                    f"{self.path}: TSV missing schema_version header (first line "
                    f"is {header[:40]!r})"
                )
            fields = header.split("\t")
            if fields != list(TSV_FIELDS):
                # tolerate reordered/renamed extras but require the core set
                missing = [f for f in TSV_FIELDS if f not in fields]
                if missing:
                    raise ValueError(
                        f"{self.path}: header missing fields {missing}"
                    )
            for lineno, line in enumerate(fh, start=2):
                line = line.rstrip("\n")
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) != len(fields):
                    sys.stderr.write(
                        f"warning: {self.path}:{lineno}: expected "
                        f"{len(fields)} columns, got {len(parts)}; skipping\n"
                    )
                    continue
                row = dict(zip(fields, parts))
                row["bytes"] = row["bytes"] or ""
                yield row

    def _rows_jsonl(self) -> Iterator[Dict[str, Any]]:
        with self._open_text() as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    sys.stderr.write(
                        f"warning: {self.path}:{lineno}: bad JSON ({exc}); skipping\n"
                    )
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("schema_version") != SCHEMA_VERSION:
                    sys.stderr.write(
                        f"warning: {self.path}:{lineno}: schema_version "
                        f"{obj.get('schema_version')!r} != {SCHEMA_VERSION!r}\n"
                    )
                yield obj

    def read_all(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for i, row in enumerate(self.rows()):
            if limit is not None and i >= limit:
                break
            out.append(row)
        return out

    def pairs(self) -> set:
        """Set of (hash, source_url) pairs (the dedup key, H8)."""
        return {(r.get("hash", ""), r.get("source_url", "")) for r in self.rows()}

    def unique_hashes(self) -> set:
        return {r.get("hash", "") for r in self.rows() if r.get("hash", "")}

    def hashes_in_order(self, limit: Optional[int] = None) -> List[str]:
        seen: set = set()
        out: List[str] = []
        for row in self.rows():
            h = row.get("hash", "")
            if h and h not in seen:
                seen.add(h)
                out.append(h)
            if limit is not None and len(out) >= limit:
                break
        return out