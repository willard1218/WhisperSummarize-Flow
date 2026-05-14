#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path
from typing import MutableMapping


def load_local_config(
    config_path: Path,
    environ: MutableMapping[str, str] | None = None,
) -> MutableMapping[str, str]:
    """Load shell-style `KEY=value` pairs into an environment mapping."""
    target = environ if environ is not None else os.environ

    if not config_path.exists():
        return target

    content = config_path.read_text(encoding="utf-8")
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export "):].strip()

        kv_part = line.split("#", 1)[0].strip()
        if "=" not in kv_part:
            continue

        key, value = kv_part.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue

        if key == "PATH":
            target["PATH"] = value.replace("$PATH", target.get("PATH", ""))
        else:
            target[key] = value

    return target
