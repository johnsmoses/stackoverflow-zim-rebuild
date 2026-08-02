"""Recovery run configuration.

All configuration flows through :class:`RecoveryConfig`. There are no
hardcoded paths anywhere in the recovery package: every directory is either
derived from ``work_root`` or set explicitly via the ``RECOVERY_*``
environment variables or a ``--config`` JSON file.

Security posture (H1): ``dry_run`` is **True** by default and ``fetch`` is
**False** by default. ``fetch_ok`` (the property every network-capable
function consults before opening a socket) is only true when the operator
explicitly passed ``--fetch`` AND did not keep the default dry-run mode.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional, Union

Pathish = Union[str, os.PathLike]

#: Hard upper bound for connection establishment (H3: connect timeout 10s).
CONNECT_TIMEOUT = 10

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _as_bool(name: str, raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    s = str(raw).strip().lower()
    if s in _TRUE_VALUES:
        return True
    if s in _FALSE_VALUES:
        return False
    raise ValueError(f"{name}: cannot interpret {raw!r} as a boolean")


def _as_int(name: str, raw: Any) -> int:
    return int(raw)


def _as_float(name: str, raw: Any) -> float:
    return float(raw)


def _as_str(name: str, raw: Any) -> str:
    return str(raw)


_FIELD_PARSERS = {
    "work_root": _as_str,
    "stage_dir": _as_str,
    "build_dir": _as_str,
    "out_dir": _as_str,
    "ia_root": _as_str,
    "recovery_root": _as_str,
    "asset_cache_dir": _as_str,
    "placeholder_bytes": _as_int,
    "user_agent": str,
    "delay": _as_float,
    "timeout": _as_float,
    "max_bytes": _as_int,
    "dry_run": _as_bool,
    "fetch": _as_bool,
    "max_redirects": _as_int,
    "max_retries": _as_int,
    "backoff_base": _as_float,
    "concurrency": _as_int,
}


@dataclasses.dataclass
class RecoveryConfig:
    """Everything a recovery step needs to know about its run.

    ``dry_run`` defaults to True and ``fetch`` defaults to False: the
    pipeline is inert by default (H1). Only ``fetch=True`` AND
    ``dry_run=False`` permits sockets.
    """

    work_root: Optional[Path] = None
    stage_dir: Optional[Path] = None
    build_dir: Optional[Path] = None
    out_dir: Optional[Path] = None
    ia_root: str = ""
    recovery_root: str = ""
    asset_cache_dir: Optional[Path] = None
    placeholder_bytes: int = 1852
    user_agent: str = (
        "stackoverflow-zim-rebuild-recovery/0.1 "
        "(image recovery pipeline; archive.org + i.sstatic.net assets)"
    )
    delay: float = 0.5
    timeout: float = 30
    max_bytes: int = 26214400
    dry_run: bool = True
    fetch: bool = False
    max_redirects: int = 5
    max_retries: int = 5
    backoff_base: float = 2.0
    concurrency: int = 1

    @property
    def fetch_ok(self) -> bool:
        """True only when the operator explicitly enabled fetching (H1)."""
        return self.fetch and not self.dry_run

    def with_overrides(self, **kwargs: Any) -> "RecoveryConfig":
        """Return a copy with the given fields replaced."""
        return dataclasses.replace(self, **kwargs)

    def resolved(self) -> "RecoveryConfig":
        """Fill in derived paths relative to ``work_root`` (never hardcoded).

        Missing directories fall back to ``work_root``-relative defaults that
        mirror the repo's ``WORK_ROOT`` convention (stage/, build/, out/,
        assets/).
        """
        work = self.work_root if self.work_root is not None else Path.cwd()
        work = Path(work).expanduser().resolve()
        stage = self.stage_dir if self.stage_dir is not None else work / "stage"
        build = self.build_dir if self.build_dir is not None else work / "build"
        out = self.out_dir if self.out_dir is not None else work / "out"
        assets = (
            self.asset_cache_dir
            if self.asset_cache_dir is not None
            else work / "assets"
        )
        return dataclasses.replace(
            self,
            work_root=work,
            stage_dir=Path(stage),
            build_dir=Path(build),
            out_dir=Path(out),
            asset_cache_dir=Path(assets),
        )

    # ------------------------------------------------------------------ #
    # construction
    # ------------------------------------------------------------------ #
    @classmethod
    def from_env(
        cls,
        config_path: Optional[Pathish] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> "RecoveryConfig":
        """Build a config from ``RECOVERY_*`` environment variables and/or a
        ``--config`` JSON file.

        Precedence (lowest to highest): dataclass defaults, JSON file keys,
        ``RECOVERY_*`` environment variables.
        """
        if env is None:
            env = os.environ

        values: dict[str, Any] = {}
        if config_path is not None:
            with open(config_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError(f"{config_path}: expected a JSON object")
            for key, raw in data.items():
                if key not in _FIELD_PARSERS:
                    raise ValueError(f"{config_path}: unknown config key {key!r}")
                values[key] = _FIELD_PARSERS[key](key, raw)

        for key, parser in _FIELD_PARSERS.items():
            env_name = "RECOVERY_" + key.upper()
            if env_name in env:
                values[key] = parser(key, env[env_name])

        return cls(**values)

    @classmethod
    def from_args(
        cls,
        args: Any,
        env: Optional[Mapping[str, str]] = None,
    ) -> "RecoveryConfig":
        """Build from an argparse namespace.

        Honors ``--config`` (JSON file), ``--dry-run`` / ``--fetch`` and any
        ``--<field>`` style override attributes the CLI exposes, then applies
        ``RECOVERY_*`` env vars.
        """
        config_path = getattr(args, "config", None)
        cfg = cls.from_env(config_path=config_path, env=env)

        overrides: dict[str, Any] = {}
        field_names = set(_FIELD_PARSERS)
        for key in field_names:
            if hasattr(args, key):
                value = getattr(args, key)
                if value is not None:
                    overrides[key] = value
        if overrides:
            cfg = cfg.with_overrides(**overrides)
        return cfg.resolved()

    def env_summary(self) -> str:
        """One-line human summary of the effective run mode."""
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        fetch = "fetch=ON" if self.fetch_ok else (
            "fetch=OFF" if not self.fetch else "fetch=ON(dry-run:no-sockets)"
        )
        return f"{mode} {fetch} work_root={self.work_root}"