#!/usr/bin/env python3

import os
import subprocess
import sys
from pathlib import Path
from typing import List

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from project_runtime import bootstrap_project, load_project_env

BASE_DIR = bootstrap_project(ROOT_DIR)

from tools.recipient_groups import load_recipient_groups, resolve_emails
from pipeline.run_registered_podcasts import (
    load_subscriptions as load_podcast_subs,
    parse_run_date,
    resolve_podcast_title,
    transcript_path_for,
    sync_podcast_latest
)
from pipeline.run_registered_youtube import (
    load_subscriptions as load_youtube_subs,
    sync_youtube_latest,
    run_command
)
from tools.notifier import get_notifiers, send_telegram_msg
from tools.logger import setup_logging, get_logger

from pipeline.downloaders import (
    BaseDownloader as DownloaderBase,
    PodcastDownloader as PodcastDownloaderImpl,
    YouTubeDownloader as YouTubeDownloaderImpl,
)
from pipeline.cli import parse_and_run
from pipeline.item_lifecycle import ItemLifecycleProcessor
from pipeline.models import DailyItem, PipelineContext, TranscriptionLock
from pipeline.runtime_ops import write_task_metadata_for_items as write_task_metadata_for_items_impl
from pipeline.task_builder import TaskBuilder
from pipeline.transcribers import BaseTranscriber, WhisperCPPTranscriber, WhisperKitTranscriber

logger = get_logger("pipeline")

def get_transcriber(args) -> BaseTranscriber:
    if args.transcriber_type == "whisperkit":
        bin_path = os.environ.get("WHISPERKIT_BIN")
        if not bin_path:
            bin_path = "whisperkit-cli"
        model_path = os.environ.get("WHISPERKIT_MODEL_PATH")
        return WhisperKitTranscriber(bin_path, model_path)
    else:
        return WhisperCPPTranscriber(args.transcribe_script)

class BaseDownloader(DownloaderBase):
    pass

class YouTubeDownloader(YouTubeDownloaderImpl, BaseDownloader):
    def __init__(self):
        super().__init__(sync_youtube_latest)

class PodcastDownloader(PodcastDownloaderImpl, BaseDownloader):
    def __init__(self):
        super().__init__(sync_podcast_latest)

def trigger_auto_fix(item_label: str, error_msg: str, log_file: str = None):
    """Triggers the autonomous auto-fixer in the background."""
    try:
        fixer_script = BASE_DIR / "tools" / "auto_fixer.py"
        if not fixer_script.exists():
            return
        cmd = [sys.executable, str(fixer_script), "--task", item_label, "--error", error_msg]
        if log_file:
            cmd += ["--log", log_file]
        subprocess.Popen(cmd)
        logger.info(f"Auto-fixer triggered for {item_label}", action="fix_trigger")
    except Exception as exc:
        logger.error(f"Failed to trigger auto-fixer: {exc}", action="fix_trigger_error")

# --- Configuration & Setup ---

def load_local_config() -> None:
    load_project_env(BASE_DIR)

def build_items(args, root: Path) -> List[DailyItem]:
    return TaskBuilder(
        args,
        root,
        BASE_DIR,
        load_recipient_groups_fn=load_recipient_groups,
        resolve_emails_fn=resolve_emails,
        load_podcast_subs_fn=load_podcast_subs,
        load_youtube_subs_fn=load_youtube_subs,
        resolve_podcast_title_fn=resolve_podcast_title,
    ).build()

def process_item_full_lifecycle(item_index: int, args, context: PipelineContext, transcribe_lock: TranscriptionLock, transcriber: BaseTranscriber):
    """Backward-compatible wrapper around the lifecycle processor."""
    ItemLifecycleProcessor(
        args,
        context,
        transcribe_lock,
        transcriber,
        base_dir=BASE_DIR,
        run_command=run_command,
        trigger_auto_fix=trigger_auto_fix,
        downloader_classes=lambda: [YouTubeDownloader, PodcastDownloader],
        transcript_path_for_fn=transcript_path_for,
        send_telegram_msg_fn=send_telegram_msg,
        get_notifiers_fn=get_notifiers,
    ).process(item_index)

def write_task_metadata_for_items(items: List[DailyItem], args) -> None:
    write_task_metadata_for_items_impl(items, args)

def main(argv=None) -> int:
    return parse_and_run(
        argv,
        base_dir=BASE_DIR,
        parse_run_date=parse_run_date,
        load_local_config=load_local_config,
        build_items=build_items,
        write_task_metadata_for_items=write_task_metadata_for_items,
        get_transcriber=get_transcriber,
        process_item_full_lifecycle=process_item_full_lifecycle,
    )

if __name__ == "__main__":
    raise SystemExit(main())
