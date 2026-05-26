#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import sys
import time
import fcntl
import hashlib
import concurrent.futures
import threading
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import List

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from project_runtime import bootstrap_project, load_project_env, resolve_project_path

BASE_DIR = bootstrap_project(ROOT_DIR)

from tools.recipient_groups import load_recipient_groups, resolve_emails
from tools.output_paths import (
    infer_podcast_slug,
    subscribed_podcast_output_dir,
    subscribed_youtube_output_dir,
    telegram_media_output_dir,
    telegram_url_output_dir,
    write_task_metadata,
    update_task_metadata,
    load_task_metadata,
    youtube_video_id,
)
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
from tools.summarize_transcript import summarize_file
from tools.notifier import get_notifiers, send_telegram_msg
from tools.logger import setup_logging, get_logger, TaskLogger

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

# --- Data Models ---

@dataclass
class DailyItem:
    label: str
    kind: str
    source_url: str
    emails: list[str]
    output_dir: Path
    prompt_file: Path | None = None
    audio_path: Path | None = None
    transcript_path: Path | None = None
    mail_attachment_path: Path | None = None
    mail_body: str = ""
    title: str = ""
    duration_str: str = ""
    processing_time_str: str = ""
    summarization_time_str: str = ""
    failed: bool = False
    download_ready: bool = False
    messages: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def log_trace(self, message: str):
        self.trace.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

# --- OCP Pipeline Architecture ---

class PipelineContext:
    def __init__(self, args, items: List[DailyItem]):
        self.args = args
        self.items = items
        self.overall_ok = True
        self.task_loggers = [TaskLogger("pipeline", item.label) for item in items]

    def report_status(self, item_index: int, message: str, level: str = "info"):
        """Logs a message with task context and optionally sends to Telegram."""
        task_logger = self.task_loggers[item_index]
        if level == "error":
            task_logger.error(message)
            self.overall_ok = False
        else:
            task_logger.info(message)

    def log_event(self, item_index: int, action: str, status: str, duration: float = 0, detail: str = ""):
        msg = f"EVENT {action} status={status} duration={duration:.2f}s"
        if detail: msg += f" detail=\"{detail}\""
        self.task_loggers[item_index].info(msg, action=action)

class BaseDownloader:
    def can_handle(self, item: DailyItem) -> bool: raise NotImplementedError
    def download(self, item: DailyItem, context: PipelineContext, item_index: int) -> bool: raise NotImplementedError

class YouTubeDownloader(BaseDownloader):
    @staticmethod
    def should_use_archive(context: PipelineContext) -> bool:
        return not bool(getattr(context.args, "url", None))

    def can_handle(self, item: DailyItem) -> bool:
        return item.kind == "youtube"
    
    def download(self, item: DailyItem, context: PipelineContext, item_index: int) -> bool:
        t_start = time.monotonic()
        use_archive = self.should_use_archive(context)
        
        res = sync_youtube_latest(item.source_url, item.output_dir, use_archive=use_archive)
        
        if res.success:
            item.audio_path = res.audio_path
            item.download_ready = True
            if res.specific_url:
                item.source_url = res.specific_url
            if res.title:
                item.title = res.title
            
            detail = res.audio_path.name if res.audio_path else "ok"
            context.log_event(item_index, "download", "ok", time.monotonic()-t_start, detail)
            return True
            
        context.log_event(item_index, "download", "skipped", time.monotonic()-t_start, "no new video")
        return False

class PodcastDownloader(BaseDownloader):
    def can_handle(self, item: DailyItem) -> bool:
        return item.kind == "podcast"
    
    def download(self, item: DailyItem, context: PipelineContext, item_index: int) -> bool:
        t_start = time.monotonic()
        
        res = sync_podcast_latest(item.source_url, item.output_dir, context.args.run_date, debug_mode=context.args.debug)
        
        if res.skipped:
            if context.args.url: # Ad-hoc task
                item.failed = True
                error_msg = f"❌ 找不到該日期的單集: {item.label}"
                item.messages.append(error_msg)
                context.log_event(item_index, "download", "failed", time.monotonic()-t_start, "no episode for specific request")
                return False
            else:
                context.log_event(item_index, "download", "skipped", time.monotonic()-t_start, "no episode")
                # Return False but don't set item.failed, effectively stopping the lifecycle for this item
                return False
            
        if res.success:
            item.audio_path = res.audio_path
            item.download_ready = True
            if res.specific_url:
                item.source_url = res.specific_url
            if res.title:
                item.title = res.title
                
            context.log_event(item_index, "download", "ok", time.monotonic()-t_start, res.audio_path.name)
            return True
        
        return False

# --- Concurrency & Locking ---

class TranscriptionLock:
    """A file-based lock to ensure only one transcription runs at a time across processes and threads."""
    def __init__(self, lock_file="/tmp/whisper_transcription.lock"):
        self.lock_file_path = Path(lock_file)
        self.lock_file = None
        self.thread_lock = threading.Lock()

    def __enter__(self):
        self.thread_lock.acquire()
        try:
            self.lock_file = open(self.lock_file_path, "w")
            fcntl.flock(self.lock_file, fcntl.LOCK_EX)
            self.lock_file.write(str(os.getpid()))
            self.lock_file.flush()
        except Exception:
            self.thread_lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.lock_file:
                fcntl.flock(self.lock_file, fcntl.LOCK_UN)
                self.lock_file.close()
                self.lock_file = None
        finally:
            self.thread_lock.release()

class ItemLifecycleProcessor:
    def __init__(self, args, context: PipelineContext, transcribe_lock: TranscriptionLock, transcriber: BaseTranscriber):
        self.args = args
        self.context = context
        self.transcribe_lock = transcribe_lock
        self.transcriber = transcriber

    def process(self, item_index: int) -> None:
        item = self.context.items[item_index]
        self._load_existing_metadata(item)
        self._prepare_mock_item(item_index, item)
        self._run_download_stage(item_index, item)
        if item.failed or not item.download_ready:
            return
        self._run_transcribe_stage(item_index, item)
        if item.failed or not item.transcript_path:
            return
        self._run_traditionalize_stage(item_index, item)
        if item.failed:
            return
        self._run_summarize_stage(item_index, item)
        if item.failed:
            return
        self._run_notify_stage(item_index, item)

    def _load_existing_metadata(self, item: DailyItem) -> None:
        metadata_path = item.output_dir / "metadata.json"
        if not metadata_path.exists():
            return
        try:
            meta = json.loads(metadata_path.read_text(encoding="utf-8"))
            item.processing_time_str = meta.get("processing_time_str", "")
            item.summarization_time_str = meta.get("summarization_time_str", "")
            item.duration_str = meta.get("duration_str", "")
            if meta.get("title") and not item.title:
                item.title = meta.get("title")
        except Exception:
            pass

    def _prepare_mock_item(self, item_index: int, item: DailyItem) -> None:
        if not self.args.mock:
            return
        item.output_dir.mkdir(parents=True, exist_ok=True)
        if not item.audio_path:
            item.audio_path = item.output_dir / "mock_audio.wav"
            if not item.audio_path.exists():
                item.audio_path.write_text("dummy audio content", encoding="utf-8")
        item.download_ready = True
        item.title = item.title or f"Mock Task {item_index}"
        self.context.report_status(item_index, "🧪 [MOCK] 下載完成")

    def _run_download_stage(self, item_index: int, item: DailyItem) -> None:
        if item.download_ready:
            return
        handled = False
        for downloader_cls in BaseDownloader.__subclasses__():
            downloader = downloader_cls()
            if not downloader.can_handle(item):
                continue
            handled = True
            try:
                download_ok = downloader.download(item, self.context, item_index)
                if download_ok:
                    if item.download_ready:
                        self.context.report_status(item_index, "✅ 下載完成")
                    return
                if not item.failed:
                    return
                error_msg = f"❌ {item.label} 下載失敗"
                self._fail_item(item_index, item, error_msg, "Download failed", f"{error_msg} (URL: {item.source_url})", notify_direct=True, trigger_fix=True)
            except Exception as exc:
                item.failed = True
                error_msg = f"❌ {item.label} 下載錯誤: {exc}"
                self.context.report_status(item_index, error_msg, level="error")
                item.messages.append(str(exc))
                tg_msg = f"{error_msg} (URL: {item.source_url})"
                send_telegram_msg(tg_msg)
                trigger_auto_fix(item.label, tg_msg, self.args.log_file)
            return
        if not handled and not item.failed:
            error_msg = f"❌ {item.label} 錯誤: 找不到支援的下載器"
            self._fail_item(item_index, item, error_msg, "No downloader found", error_msg, notify_direct=True)

    def _run_transcribe_stage(self, item_index: int, item: DailyItem) -> None:
        existing = transcript_path_for(item.audio_path) if item.audio_path else None
        if self.args.enable_transcribe:
            summary_hant = existing.with_name(existing.name.replace(".srt.txt", ".zh-Hant.summary.md")) if existing else None
            is_stale = bool(existing and existing.exists() and item.audio_path.stat().st_mtime > existing.stat().st_mtime)
            if (existing and existing.exists() and not is_stale) or (summary_hant and summary_hant.exists() and not is_stale):
                item.transcript_path = existing
                meta = load_task_metadata(item.output_dir)
                item.processing_time_str = meta.get("processing_time_str", "Cached")
                if item.audio_path and item.audio_path.exists():
                    item.duration_str = self.transcriber.get_audio_duration(item.audio_path)
                self.context.report_status(item_index, "⏭️ 轉錄已存在")
                return
            if self.args.mock:
                self._write_mock_transcript(item_index, item)
                return
            self._transcribe_with_lock(item_index, item, is_stale)
            return
        if existing and existing.exists():
            item.transcript_path = existing
            self.context.report_status(item_index, "⏭️ 轉錄已存在 (transcribe disabled)")

    def _write_mock_transcript(self, item_index: int, item: DailyItem) -> None:
        item.transcript_path = item.output_dir / f"{item.audio_path.stem}.srt.txt"
        item.transcript_path.write_text("1\n00:00:00,000 --> 00:00:05,000\n[A] This is a mock transcription for testing purposes.\n", encoding="utf-8")
        txt_path = item.transcript_path.with_name(f"{item.transcript_path.stem.replace('.srt', '')}.txt")
        txt_path.write_text("[A] This is a mock transcription for testing purposes.\n", encoding="utf-8")
        item.duration_str = "00:00:05"
        item.processing_time_str = "00:00:01"
        update_task_metadata(item.output_dir, {
            "processing_time_str": item.processing_time_str,
            "duration_str": item.duration_str,
        })
        self.context.report_status(item_index, "🧪 [MOCK] 轉錄完成")

    def _transcribe_with_lock(self, item_index: int, item: DailyItem, is_stale: bool) -> None:
        if is_stale:
            self.context.report_status(item_index, "🔄 偵測到音檔更新，重新轉錄")
        self.context.report_status(item_index, "⏳ 等待轉錄資源...")
        with self.transcribe_lock:
            self.context.report_status(item_index, f"🎙️ 開始轉錄 ({self.args.transcriber_type})")
            t_start = time.monotonic()
            item.transcript_path = self.transcriber.transcribe(item.audio_path, item.output_dir)
            if item.transcript_path and item.transcript_path.exists():
                duration_secs = time.monotonic() - t_start
                self.context.report_status(item_index, "✅ 轉錄完成")
                self.context.log_event(item_index, "transcribe", "ok", duration_secs, item.transcript_path.name)
                h = int(duration_secs // 3600)
                m = int((duration_secs % 3600) // 60)
                s = int(duration_secs % 60)
                item.processing_time_str = f"{h:02}:{m:02}:{s:02}"
                if item.audio_path and item.audio_path.exists():
                    item.duration_str = self.transcriber.get_audio_duration(item.audio_path)
                update_task_metadata(item.output_dir, {
                    "processing_time_str": item.processing_time_str,
                    "duration_str": item.duration_str,
                })
                return
            error_msg = f"❌ {item.label} 轉錄失敗"
            detail = f"{error_msg} (Audio: {item.audio_path.name if item.audio_path else 'unknown'})"
            self._fail_item(item_index, item, error_msg, "Transcription failed", detail, notify_direct=True, trigger_fix=True)

    def _run_traditionalize_stage(self, item_index: int, item: DailyItem) -> None:
        if not self.args.enable_traditionalize:
            return
        t_start = time.monotonic()
        conv_script = BASE_DIR / "tools" / "convert_transcript_opencc.py"
        try:
            if item.transcript_path.name.endswith(".srt.txt") and not item.transcript_path.name.endswith(".zh-Hant.srt.txt"):
                hant = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.srt.txt"))
                if not (hant.exists() and hant.stat().st_mtime >= item.transcript_path.stat().st_mtime):
                    res = run_command([sys.executable, str(conv_script), str(item.transcript_path), "--output-path", str(hant), "--config", self.args.opencc_config])
                    if res.returncode != 0:
                        raise RuntimeError(res.stderr.strip() or f"OpenCC conversion failed for SRT (status {res.returncode})")
            txt_path = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".txt"))
            if txt_path.exists() and not txt_path.name.endswith(".zh-Hant.txt"):
                txt_hant = txt_path.with_name(txt_path.name[:-4] + ".zh-Hant.txt")
                if not (txt_hant.exists() and txt_hant.stat().st_mtime >= txt_path.stat().st_mtime):
                    res = run_command([sys.executable, str(conv_script), str(txt_path), "--output-path", str(txt_hant), "--config", self.args.opencc_config])
                    if res.returncode != 0:
                        raise RuntimeError(res.stderr.strip() or f"OpenCC conversion failed for TXT (status {res.returncode})")
            hant_srt = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.srt.txt"))
            if hant_srt.exists():
                item.mail_attachment_path = hant_srt
            self.context.log_event(item_index, "traditionalize", "ok", time.monotonic() - t_start)
        except Exception as exc:
            error_msg = f"❌ {item.label} 繁體化失敗: {exc}"
            self._fail_item(item_index, item, error_msg, error_msg, error_msg, notify_direct=True, trigger_fix=True)

    def _run_summarize_stage(self, item_index: int, item: DailyItem) -> None:
        if not self.args.enable_summarize:
            return
        txt_hant = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.txt"))
        txt_plain = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".txt"))
        target_txt = txt_hant if (self.args.enable_traditionalize and txt_hant.exists()) else (txt_plain if txt_plain.exists() else txt_hant)
        if not (target_txt and target_txt.exists()):
            error_msg = f"❌ {item.label} 找不到摘要所需的文字檔"
            self._fail_item(item_index, item, error_msg, error_msg, error_msg, notify_direct=True, trigger_fix=True)
            return
        meta = load_task_metadata(item.output_dir)
        if not item.summarization_time_str:
            item.summarization_time_str = meta.get("summarization_time_str", "")
        t_start = time.monotonic()
        try:
            if self.args.mock:
                summary_path = target_txt.with_name(f"{target_txt.stem}.summary.md")
                summary_path.write_text("# [MOCK] Summary\n- This is a mock summary for rapid testing.", encoding="utf-8")
                duration_secs = 0.5
            else:
                summary_path = summarize_file(target_txt, item.prompt_file)
                duration_secs = time.monotonic() - t_start
            if not (summary_path and summary_path.exists()):
                raise RuntimeError(f"Summary result empty for {item.label}")
            item.mail_body = summary_path.read_text(encoding="utf-8")
            if duration_secs > 1.0 or not item.summarization_time_str:
                h = int(duration_secs // 3600)
                m = int((duration_secs % 3600) // 60)
                s = int(duration_secs % 60)
                item.summarization_time_str = f"{h:02}:{m:02}:{s:02}"
                update_task_metadata(item.output_dir, {"summarization_time_str": item.summarization_time_str})
                self.context.report_status(item_index, "✅ 摘要完成" if not self.args.mock else "🧪 [MOCK] 摘要完成")
            else:
                self.context.report_status(item_index, "⏭️ 摘要已存在")
            self.context.log_event(item_index, "summarize", "ok", duration_secs, summary_path.name)
        except Exception as exc:
            error_msg = f"❌ {item.label} 摘要失敗: {exc}"
            self._fail_item(item_index, item, error_msg, error_msg, error_msg, notify_direct=True, trigger_fix=True)

    def _run_notify_stage(self, item_index: int, item: DailyItem) -> None:
        if not item.mail_attachment_path and item.transcript_path:
            item.mail_attachment_path = item.transcript_path
        for notifier in get_notifiers():
            if not notifier.is_enabled(self.args):
                continue
            try:
                notifier.notify([item], self.args)
            except Exception as exc:
                error_msg = f"❌ {item.label} 通知失敗 ({notifier.name}): {exc}"
                self.context.report_status(item_index, error_msg, level="error")
                if notifier.name != "telegram":
                    send_telegram_msg(error_msg)

    def _fail_item(
        self,
        item_index: int,
        item: DailyItem,
        error_msg: str,
        item_message: str,
        telegram_message: str | None = None,
        *,
        notify_direct: bool = False,
        trigger_fix: bool = False,
    ) -> None:
        item.failed = True
        self.context.report_status(item_index, error_msg, level="error")
        item.messages.append(item_message)
        if notify_direct and telegram_message:
            send_telegram_msg(telegram_message)
        if trigger_fix and telegram_message:
            trigger_auto_fix(item.label, telegram_message, self.args.log_file)


class TaskBuilder:
    def __init__(self, args, root: Path):
        self.args = args
        self.root = root
        self.groups = load_recipient_groups(resolve_project_path(BASE_DIR, args.recipient_config))

    def build(self) -> List[DailyItem]:
        if self.args.url:
            return [self._build_url_item()]
        if getattr(self.args, "local_file", None):
            return [self._build_local_file_item()]
        return self._build_subscription_items()

    def _build_url_item(self) -> DailyItem:
        emails = resolve_emails({"recipient_group": self.args.recipient_group}, self.groups)
        kind = "youtube" if "youtube.com" in self.args.url or "youtu.be" in self.args.url else "podcast"
        if getattr(self.args, "task_origin", "") == "telegram":
            output_dir = telegram_url_output_dir(self.root, self.args.url)
        elif kind == "youtube":
            output_dir = self.root / f"adhoc_yt_{youtube_video_id(self.args.url) or 'unknown'}"
        else:
            output_dir = self.root / f"adhoc_podcast_{infer_podcast_slug(self.args.url)}"
        return DailyItem(label="Ad-hoc Task", kind=kind, source_url=self.args.url, emails=emails, output_dir=output_dir)

    def _build_local_file_item(self) -> DailyItem:
        local_path = Path(self.args.local_file).expanduser().resolve()
        emails = resolve_emails({"recipient_group": self.args.recipient_group}, self.groups)
        suffix = local_path.suffix.lower()
        kind = "video" if suffix in {".mp4", ".mov", ".mkv"} else "voice"
        return DailyItem(
            label="Voice Message",
            kind=kind,
            source_url=f"file://{local_path}",
            emails=emails,
            output_dir=local_path.parent,
            audio_path=local_path,
            download_ready=True,
        )

    def _build_subscription_items(self) -> List[DailyItem]:
        items: list[DailyItem] = []
        pod_cfg = resolve_project_path(BASE_DIR, self.args.podcast_config)
        yt_cfg = resolve_project_path(BASE_DIR, self.args.youtube_config)
        for index, sub in enumerate(load_podcast_subs(pod_cfg), 1):
            rss = sub.get("rss_url", "").strip()
            title = sub.get("podcast_title", "").strip() or (resolve_podcast_title(rss) if rss else "")
            prompt_file = BASE_DIR / sub["prompt_file"] if sub.get("prompt_file") else None
            items.append(
                DailyItem(
                    f"Podcast {index}",
                    "podcast",
                    sub.get("podcast_url", rss),
                    resolve_emails(sub, self.groups),
                    subscribed_podcast_output_dir(self.root, title or "podcast"),
                    prompt_file=prompt_file,
                    title=title,
                )
            )
        for index, sub in enumerate(load_youtube_subs(yt_cfg), 1):
            url = sub.get("channel_url", "").strip()
            prompt_file = BASE_DIR / sub["prompt_file"] if sub.get("prompt_file") else None
            items.append(
                DailyItem(
                    f"YouTube {index}" if index > 1 else "YouTube",
                    "youtube",
                    url,
                    resolve_emails(sub, self.groups),
                    subscribed_youtube_output_dir(self.root, url),
                    prompt_file=prompt_file,
                )
            )
        return items

def trigger_auto_fix(item_label: str, error_msg: str, log_file: str = None):
    """Triggers the autonomous auto-fixer in the background."""
    try:
        fixer_script = BASE_DIR / "tools" / "auto_fixer.py"
        if not fixer_script.exists():
            return
            
        cmd = [sys.executable, str(fixer_script), "--task", item_label, "--error", error_msg]
        if log_file:
            cmd += ["--log", log_file]
        
        # Launch in background to not stall the rest of the pipeline
        subprocess.Popen(cmd)
        logger.info(f"Auto-fixer triggered for {item_label}", action="fix_trigger")
    except Exception as e:
        logger.error(f"Failed to trigger auto-fixer: {e}", action="fix_trigger_error")

# --- Configuration & Setup ---

def load_local_config() -> None:
    load_project_env(BASE_DIR)

def build_items(args, root: Path) -> List[DailyItem]:
    return TaskBuilder(args, root).build()

def process_item_full_lifecycle(item_index: int, args, context: PipelineContext, transcribe_lock: TranscriptionLock, transcriber: BaseTranscriber):
    """Backward-compatible wrapper around the lifecycle processor."""
    ItemLifecycleProcessor(args, context, transcribe_lock, transcriber).process(item_index)

def write_task_metadata_for_items(items: List[DailyItem], args) -> None:
    if getattr(args, "task_origin", "") != "telegram":
        return
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    created_at = datetime.now().astimezone().isoformat()
    for item in items:
        payload = {"task_origin": args.task_origin, "kind": item.kind, "source_url": item.source_url, "created_at": created_at, "chat_id": chat_id, "title": item.title, "label": item.label}
        if item.kind == "youtube": payload["video_id"] = youtube_video_id(item.source_url) or ""
        update_task_metadata(item.output_dir, payload)

def main() -> int:
    load_local_config()
    parser = argparse.ArgumentParser(description="Daily pipeline.")
    parser.add_argument("--url", help="Ad-hoc URL to process")
    parser.add_argument("--local-file", help="Local audio file to process")
    parser.add_argument("--recipient-group", default="all", help="Recipient group for ad-hoc task")
    parser.add_argument("--date", dest="run_date", type=parse_run_date, default=date.today())
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--log-file", help="Path to the log file being used (for auto-fixer)")
    parser.add_argument("--transcribe-script", default=os.environ.get("GENSRT_SCRIPT", str(BASE_DIR / "gensrt.sh")))
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
    
    args = parser.parse_args()
    setup_logging(level=logging.DEBUG if args.debug else logging.INFO, format_type="kv")
    
    if args.telegram_chat_id: os.environ["TELEGRAM_CHAT_ID"] = args.telegram_chat_id
    
    root = Path(args.output_root).expanduser().resolve()
    items = build_items(args, root)
    write_task_metadata_for_items(items, args)
    
    if args.debug:
        debug_email = os.environ.get("DEBUG_RECIPIENT")
        if debug_email:
            for item in items: item.emails = [debug_email]
        debug_telegram = os.environ.get("DEBUG_TELEGRAM_CHAT_ID")
        if debug_telegram: os.environ["TELEGRAM_CHAT_ID"] = debug_telegram

    if not items:
        logger.info("No tasks to process.")
        return 0

    context = PipelineContext(args, items)
    transcribe_lock = TranscriptionLock()
    transcriber = get_transcriber(args)

    logger.info(f"Starting concurrent pipeline concurrency={args.concurrency} transcriber={args.transcriber_type}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(process_item_full_lifecycle, i, args, context, transcribe_lock, transcriber) for i in range(len(items))]
        concurrent.futures.wait(futures)

    all_downloaded = all(item.download_ready for item in items)
    logger.info(f"Pipeline finished overall_ok={context.overall_ok} all_downloaded={all_downloaded}")

    return 0 if context.overall_ok else 1

if __name__ == "__main__":
    import logging
    raise SystemExit(main())
