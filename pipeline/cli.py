from __future__ import annotations

import argparse
import concurrent.futures
import logging
import os
from datetime import date
from pathlib import Path
from typing import Callable

from pipeline.models import PipelineContext, TranscriptionLock
from pipeline.runtime_ops import cleanup_output_capacity
from tools.logger import get_logger, setup_logging


logger = get_logger("pipeline")


def build_parser(*, base_dir: Path, parse_run_date: Callable[[str], date]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Daily pipeline.")
    parser.add_argument("--url", help="Ad-hoc URL to process")
    parser.add_argument("--local-file", help="Local audio file to process")
    parser.add_argument("--recipient-group", default="all", help="Recipient group for ad-hoc task")
    parser.add_argument("--date", dest="run_date", type=parse_run_date, default=date.today())
    parser.add_argument("--output-root")
    parser.add_argument("--log-file", help="Path to the log file being used (for auto-fixer)")
    parser.add_argument("--transcribe-script")
    parser.add_argument("--podcast-config", default="config/subscriptions.json")
    parser.add_argument("--youtube-config", default="config/youtube_subscriptions.json")
    parser.add_argument("--recipient-config", default=os.environ.get("RECIPIENT_CONFIG_FILE", "config/recipient_groups.local.json"))
    parser.add_argument("--traditionalize-transcript", dest="enable_traditionalize", action="store_true")
    parser.add_argument("--opencc-config")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--mock", action="store_true", help="Run the pipeline with dummy files for rapid testing")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--enable-transcribe", type=int)
    parser.add_argument("--enable-summarize", type=int)
    parser.add_argument("--enable-mail", type=int)
    parser.add_argument("--enable-telegram", type=int)
    parser.add_argument("--telegram-progress", action="store_true")
    parser.add_argument("--telegram-chat-id", help="Override chat ID")
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--transcriber-type", choices=["whisperkit", "whispercpp"])
    parser.add_argument("--task-origin", default="default", choices=["default", "telegram"])
    parser.add_argument("--max-output-daily-bytes", type=int)
    parser.add_argument("--max-output-telegram-bytes", type=int)
    return parser


def apply_runtime_overrides(args, items) -> None:
    if args.telegram_chat_id:
        os.environ["TELEGRAM_CHAT_ID"] = args.telegram_chat_id
    if not args.debug:
        return
    debug_email = os.environ.get("DEBUG_RECIPIENT")
    if debug_email:
        for item in items:
            item.emails = [debug_email]
    debug_telegram = os.environ.get("DEBUG_TELEGRAM_CHAT_ID")
    if debug_telegram:
        os.environ["TELEGRAM_CHAT_ID"] = debug_telegram


def run_pipeline(args, items, *, get_transcriber, process_item_full_lifecycle) -> int:
    if not items:
        logger.info("No tasks to process.")
        return 0

    context = PipelineContext(args, items)
    transcribe_lock = TranscriptionLock()
    transcriber = get_transcriber(args)

    logger.info(f"Starting concurrent pipeline concurrency={args.concurrency} transcriber={args.transcriber_type}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(process_item_full_lifecycle, i, args, context, transcribe_lock, transcriber)
            for i in range(len(items))
        ]
        concurrent.futures.wait(futures)

    all_downloaded = all(item.download_ready for item in items)
    logger.info(f"Pipeline finished overall_ok={context.overall_ok} all_downloaded={all_downloaded}")

    # Capacity Enforcement
    try:
        limit = args.max_output_telegram_bytes if args.task_origin == "telegram" else args.max_output_daily_bytes
        cleanup_output_capacity(Path("."), args.task_origin, limit)
    except Exception as e:
        logger.error(f"Failed to enforce output capacity: {e}", action="capacity_cleanup_error")

    return 0 if context.overall_ok else 1


def parse_and_run(
    argv=None,
    *,
    base_dir: Path,
    parse_run_date: Callable[[str], date],
    load_local_config: Callable[[], None],
    build_items: Callable[[object, Path], list],
    write_task_metadata_for_items: Callable[[list, object], None],
    get_transcriber: Callable[[object], object],
    process_item_full_lifecycle: Callable[[int, object, PipelineContext, TranscriptionLock, object], None],
) -> int:
    load_local_config()
    
    # 1. Load config through AppConfig to enforce presence of all settings
    from tools.config_models import AppConfig
    try:
        # Pydantic will populate fields from WS_* environment variables
        # It will raise an error if any required field (no default in AppConfig) is missing.
        app_config = AppConfig()
    except Exception as e:
        setup_logging(level=logging.INFO, format_type="kv")
        logger.error(f"Configuration Error: Missing or invalid settings. Ensure all WS_* variables are set in config/local_config.sh. Error: {e}", action="config_load_failed")
        return 1

    parser = build_parser(base_dir=base_dir, parse_run_date=parse_run_date)
    args = parser.parse_args(argv)
    setup_logging(level=logging.DEBUG if args.debug else logging.INFO, format_type="kv", log_file=args.log_file)

    # 2. Map AppConfig values to args if not explicitly provided via CLI
    if args.output_root is None:
        args.output_root = app_config.output_root
    if args.transcribe_script is None:
        args.transcribe_script = app_config.transcribe_script
    if args.concurrency is None:
        args.concurrency = app_config.default_concurrency
    if args.transcriber_type is None:
        args.transcriber_type = app_config.default_transcriber
    if args.max_output_daily_bytes is None:
        args.max_output_daily_bytes = app_config.max_output_daily_bytes
    if args.max_output_telegram_bytes is None:
        args.max_output_telegram_bytes = app_config.max_output_telegram_bytes
    
    # Map toggles and tool paths
    if args.opencc_config is None:
        args.opencc_config = app_config.opencc_config
    
    # Set Binary paths into environment for subprocesses to use
    os.environ["YT_DLP_BIN"] = app_config.yt_dlp_bin
    os.environ["FFMPEG_BIN"] = app_config.ffmpeg_bin
    os.environ["FFPROBE_BIN"] = app_config.ffprobe_bin
    os.environ["OPENCC_BIN"] = app_config.opencc_bin
    os.environ["WHISPERKIT_BIN"] = app_config.whisperkit_bin
    
    # Ensure environment-based settings from AppConfig are synced
    if app_config.telegram_chat_id:
        os.environ["TELEGRAM_CHAT_ID"] = app_config.telegram_chat_id
    if app_config.telegram_bot_token:
        os.environ["TELEGRAM_BOT_TOKEN"] = app_config.telegram_bot_token

    root = Path(args.output_root).expanduser().resolve()
    items = build_items(args, root)
    write_task_metadata_for_items(items, args)
    apply_runtime_overrides(args, items)
    return run_pipeline(
        args,
        items,
        get_transcriber=get_transcriber,
        process_item_full_lifecycle=process_item_full_lifecycle,
    )
