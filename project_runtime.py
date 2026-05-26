from __future__ import annotations

import os
import sys
from pathlib import Path


def project_root_from(file_path: str | Path) -> Path:
    path = Path(file_path).resolve()
    return path if path.is_dir() else path.parent


def bootstrap_project(file_path: str | Path) -> Path:
    root = project_root_from(file_path)
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root


def load_project_env(root: Path) -> None:
    from tools.local_config import load_local_config

    load_local_config(root / "config" / "local_config.sh", os.environ)


def resolve_project_path(root: Path, value: str | Path) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (root / candidate).resolve()
