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


def get_dir_size(path: Path) -> int:
    """Calculates total size of a directory in bytes."""
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.glob("**/*") if f.is_file())


def cleanup_output_capacity(base_dir: Path, task_origin: str, max_bytes: int) -> None:
    """
    Enforces capacity limits on output folders by deleting the oldest content.
    - Daily: output/podcast and output/youtube
    - Telegram: output/telegram
    """
    output_root = base_dir / "output"
    if task_origin == "telegram":
        roots = [output_root / "telegram"]
        label = "Telegram 任務"
    else:
        # We exclude telegram from the daily count even if it's inside output/
        roots = [output_root / "podcast", output_root / "youtube"]
        label = "每日任務"

    current_size = sum(get_dir_size(r) for r in roots)
    if current_size <= max_bytes:
        return

    logger.info(f"已達上限 {current_size} bytes (限制: {max_bytes}), 開始清理 {label}", action="capacity_cleanup")

    while current_size > max_bytes:
        # Find candidates for deletion
        # Candidate format: (mtime, size, list_of_paths_to_delete, display_name)
        candidates = []

        if task_origin == "telegram":
            # For telegram, items are task folders
            # output/telegram/youtube/channel/vid
            # output/telegram/audio/ts_slug
            # output/telegram/apple_podcast/slug
            for root in roots:
                if not root.exists(): continue
                # Look for leaf-ish directories (depth 2 or 3 depending on type)
                for p in root.glob("*/*"):
                    if p.is_dir():
                        # Check if it has subdirs
                        subdirs = [d for d in p.iterdir() if d.is_dir() and d.name != "reports"]
                        if not subdirs:
                            # This is a task folder
                            mtime = p.stat().st_mtime
                            size = get_dir_size(p)
                            candidates.append((mtime, size, [p], p.name))
                        else:
                            # Check one level deeper for youtube/channel/vid
                            for subp in subdirs:
                                if subp.is_dir():
                                    mtime = subp.stat().st_mtime
                                    size = get_dir_size(subp)
                                    candidates.append((mtime, size, [subp], subp.name))
        else:
            # For daily, items are groups of files sharing a prefix (usually ends with __vid or just the .mp3)
            for root in roots:
                if not root.exists(): continue
                for channel_dir in root.iterdir():
                    if not channel_dir.is_dir(): continue
                    # Group files by their base name (everything before .mp3, .txt, etc.)
                    # We use .mp3 as the anchor for a "program"
                    for mp3 in channel_dir.glob("*.mp3"):
                        prefix = mp3.stem
                        mtime = mp3.stat().st_mtime
                        related_files = list(channel_dir.glob(f"{prefix}*"))
                        size = sum(f.stat().st_size for f in related_files if f.is_file())
                        candidates.append((mtime, size, related_files, prefix))

        if not candidates:
            logger.warning(f"無法在 {label} 中找到可刪除的項目，但容量仍超出限制", action="capacity_cleanup_failed")
            break

        # Sort by mtime (oldest first)
        candidates.sort(key=lambda x: x[0])
        oldest_mtime, oldest_size, paths_to_delete, name = candidates[0]

        logger.info(f"已刪除最舊的節目: {name}", action="capacity_prune", size=oldest_size)
        
        for p in paths_to_delete:
            try:
                if p.is_dir():
                    import shutil
                    shutil.rmtree(p)
                else:
                    p.unlink()
            except Exception as e:
                logger.error(f"無法刪除 {p}: {e}", action="capacity_prune_error")

        current_size -= oldest_size

    logger.info(f"清理完成, 當前容量: {current_size} bytes", action="capacity_cleanup_done")


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
