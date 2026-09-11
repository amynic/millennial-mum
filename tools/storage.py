"""Resolve writable paths for small JSON-backed tools."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def data_file(filename: str, *, copy_packaged_default: bool = False) -> str:
    """Return a writable data path, optionally seeded from the packaged file."""
    configured_dir = os.getenv("MM_DATA_DIR")
    if not configured_dir:
        return str(_PROJECT_ROOT / filename)

    data_dir = Path(configured_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / filename
    packaged = _PROJECT_ROOT / filename
    if copy_packaged_default and not target.exists() and packaged.exists():
        shutil.copyfile(packaged, target)
    return str(target)
