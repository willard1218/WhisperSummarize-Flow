from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

from pipeline.models import DailyItem
from tools.logger import get_logger
from tools.output_paths import update_task_metadata, youtube_video_id

logger = get_logger("pipeline")


def trigger_auto_fix(base_dir: Path, item_label: str, error_msg: str, log_file: str | None = None) -> None:
    """Triggers the autonomous auto-fixer in the background."""
    try:
        fixer_script = base_dir / "tools" / "auto_fixer.py"
        if not fixer_script.exists():
            return
        cmd = [sys.executable, str(fixer_script), "--task", item_label, "--error", error_msg]
        if log_file:
            cmd += ["--log", log_file]
        subprocess.Popen(cmd)
        logger.info(f"Auto-fixer triggered for {item_label}", action="fix_trigger")
    except Exception as exc:
        logger.error(f"Failed to trigger auto-fixer: {exc}", action="fix_trigger_error")


def write_task_metadata_for_items(items: Iterable[DailyItem], args) -> None:
    if getattr(args, "task_origin", "") != "telegram":
        return
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    created_at = datetime.now().astimezone().isoformat()
    for item in items:
        payload = {
            "task_origin": args.task_origin,
            "kind": item.kind,
            "source_url": item.source_url,
            "created_at": created_at,
            "chat_id": chat_id,
            "title": item.title,
            "label": item.label,
        }
        if item.kind == "youtube":
            payload["video_id"] = youtube_video_id(item.source_url) or ""
        update_task_metadata(item.output_dir, payload)
