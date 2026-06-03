from __future__ import annotations

import argparse
import concurrent.futures
import logging
import os
from datetime import date
from pathlib import Path
from typing import Callable

from pipeline.models import PipelineContext, TranscriptionLock
from tools.logger import get_logger, setup_logging


logger = get_logger("pipeline")


def build_parser(*, base_dir: Path, parse_run_date: Callable[[str], date]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Daily pipeline.")
    parser.add_argument("--url", help="Ad-hoc URL to process")
    parser.add_argument("--local-file", help="Local audio file to process")
    parser.add_argument("--recipient-group", default="all", help="Recipient group for ad-hoc task")
    parser.add_argument("--date", dest="run_date", type=parse_run_date, default=date.today())
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--log-file", help="Path to the log file being used (for auto-fixer)")
    parser.add_argument("--transcribe-script", default=os.environ.get("GENSRT_SCRIPT", str(base_dir / "gensrt.sh")))
    parser.add_argument("--podcast-config", default="config/subscriptions.json")
    parser.add_argument("--youtube-config", default="config/youtube_subscriptions.json")
    parser.add_argument("--recipient-config", default=os.environ.get("RECIPIENT_CONFIG_FILE", "config/recipient_groups.local.json"))
    parser.add_argument("--traditionalize-transcript", dest="enable_traditionalize", action="store_true", default=os.environ.get("ENABLE_TRADITIONALIZE", "1") == "1")
    parser.add_argument("--opencc-config", default=os.environ.get("OPENCC_CONFIG", "s2twp.json"))
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--mock", action="store_true", help="Run the pipeline with dummy files for rapid testing")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--enable-transcribe", type=int, default=int(os.environ.get("ENABLE_TRANSCRIBE", "1")))
    parser.add_argument("--enable-summarize", type=int, default=int(os.environ.get("ENABLE_SUMMARIZE", "1")))
    parser.add_argument("--enable-mail", type=int, default=int(os.environ.get("ENABLE_MAIL", "1")))
    parser.add_argument("--enable-telegram", type=int, default=int(os.environ.get("ENABLE_TELEGRAM", "1")))
    parser.add_argument("--telegram-progress", action="store_true")
    parser.add_argument("--telegram-chat-id", help="Override chat ID")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--transcriber-type", default="whisperkit", choices=["whisperkit", "whispercpp"])
    parser.add_argument("--task-origin", default="default", choices=["default", "telegram"])
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
    parser = build_parser(base_dir=base_dir, parse_run_date=parse_run_date)
    args = parser.parse_args(argv)
    setup_logging(level=logging.DEBUG if args.debug else logging.INFO, format_type="kv", log_file=args.log_file)

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
