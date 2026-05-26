from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Callable, Iterable

from pipeline.models import DailyItem, PipelineContext, TranscriptionLock
from pipeline.run_registered_podcasts import transcript_path_for
from tools.notifier import get_notifiers, send_telegram_msg
from tools.output_paths import load_task_metadata, update_task_metadata
from tools.summarize_transcript import summarize_file


class ItemLifecycleProcessor:
    def __init__(
        self,
        args,
        context: PipelineContext,
        transcribe_lock: TranscriptionLock,
        transcriber,
        *,
        base_dir: Path,
        run_command: Callable[[list[str]], object],
        trigger_auto_fix: Callable[[str, str, str | None], None],
        downloader_classes: Callable[[], Iterable[type]],
        transcript_path_for_fn: Callable[[Path], Path] = transcript_path_for,
        send_telegram_msg_fn: Callable[[str], bool] = send_telegram_msg,
        get_notifiers_fn: Callable[[], list] = get_notifiers,
    ):
        self.args = args
        self.context = context
        self.transcribe_lock = transcribe_lock
        self.transcriber = transcriber
        self.base_dir = base_dir
        self.run_command = run_command
        self.trigger_auto_fix = trigger_auto_fix
        self.downloader_classes = downloader_classes
        self.transcript_path_for_fn = transcript_path_for_fn
        self.send_telegram_msg_fn = send_telegram_msg_fn
        self.get_notifiers_fn = get_notifiers_fn

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
        for downloader_cls in self.downloader_classes():
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
                self.send_telegram_msg_fn(tg_msg)
                self.trigger_auto_fix(item.label, tg_msg, self.args.log_file)
            return
        if not handled and not item.failed:
            error_msg = f"❌ {item.label} 錯誤: 找不到支援的下載器"
            self._fail_item(item_index, item, error_msg, "No downloader found", error_msg, notify_direct=True)

    def _run_transcribe_stage(self, item_index: int, item: DailyItem) -> None:
        existing = self.transcript_path_for_fn(item.audio_path) if item.audio_path else None
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
        conv_script = self.base_dir / "tools" / "convert_transcript_opencc.py"
        try:
            if item.transcript_path.name.endswith(".srt.txt") and not item.transcript_path.name.endswith(".zh-Hant.srt.txt"):
                hant = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".zh-Hant.srt.txt"))
                if not (hant.exists() and hant.stat().st_mtime >= item.transcript_path.stat().st_mtime):
                    res = self.run_command([sys.executable, str(conv_script), str(item.transcript_path), "--output-path", str(hant), "--config", self.args.opencc_config])
                    if res.returncode != 0:
                        raise RuntimeError(res.stderr.strip() or f"OpenCC conversion failed for SRT (status {res.returncode})")
            txt_path = item.transcript_path.with_name(item.transcript_path.name.replace(".srt.txt", ".txt"))
            if txt_path.exists() and not txt_path.name.endswith(".zh-Hant.txt"):
                txt_hant = txt_path.with_name(txt_path.name[:-4] + ".zh-Hant.txt")
                if not (txt_hant.exists() and txt_hant.stat().st_mtime >= txt_path.stat().st_mtime):
                    res = self.run_command([sys.executable, str(conv_script), str(txt_path), "--output-path", str(txt_hant), "--config", self.args.opencc_config])
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
        for notifier in self.get_notifiers_fn():
            if not notifier.is_enabled(self.args):
                continue
            try:
                notifier.notify([item], self.args)
            except Exception as exc:
                error_msg = f"❌ {item.label} 通知失敗 ({notifier.name}): {exc}"
                self.context.report_status(item_index, error_msg, level="error")
                if notifier.name != "telegram":
                    self.send_telegram_msg_fn(error_msg)

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
            self.send_telegram_msg_fn(telegram_message)
        if trigger_fix and telegram_message:
            self.trigger_auto_fix(item.label, telegram_message, self.args.log_file)
