"""Конфиги и метаданные запуска: хеш конфигов, коммит, версии пакетов."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

import yaml

from .config import ROOT

CONFIGS = ROOT / "configs"
PACKAGES = ["numpy", "scipy", "pandas", "scikit-learn", "xgboost", "statsmodels", "rasterio", "shapely", "h3"]


def load_yaml(name: str) -> dict:
    return yaml.safe_load((CONFIGS / name).read_text(encoding="utf-8"))


def config_hash(*names: str) -> str:
    h = hashlib.sha256()
    for n in sorted(names):
        h.update((CONFIGS / n).read_bytes())
    return h.hexdigest()[:12]


def git_commit() -> str | None:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                             timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return rev + ("+dirty" if dirty else "") if rev else None
    except Exception:
        return None


def run_meta(*config_names: str, **extra) -> dict:
    versions = {}
    for p in PACKAGES:
        try:
            versions[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            pass
    return {
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": git_commit(),
        "configs": {n: config_hash(n) for n in config_names},
        "python": platform.python_version(),
        "packages": versions,
        **extra,
    }


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=_default), encoding="utf-8")


def _default(o):
    import numpy as np

    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))
