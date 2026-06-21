from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from pipeline.models import DailyItem, PipelineContext
from tools.output_paths import sanitize_path_segment, update_task_metadata


class BaseDownloader:
    def can_handle(self, item: DailyItem) -> bool:
        raise NotImplementedError

    def download(self, item: DailyItem, context: PipelineContext, item_index: int) -> bool:
        raise NotImplementedError


class YouTubeDownloader(BaseDownloader):
    def __init__(self, sync_youtube_latest_fn: Callable[[str, object, bool], object]):
        self.sync_youtube_latest_fn = sync_youtube_latest_fn

    @staticmethod
    def should_use_archive(context: PipelineContext) -> bool:
        return not bool(getattr(context.args, "url", None))

    def can_handle(self, item: DailyItem) -> bool:
        return item.kind == "youtube"

    def download(self, item: DailyItem, context: PipelineContext, item_index: int) -> bool:
        t_start = time.monotonic()
        use_archive = self.should_use_archive(context)
        print(f"DEBUG: YouTubeDownloader.download use_archive={use_archive} url={getattr(context.args, 'url', 'MISSING')}")
        res = self.sync_youtube_latest_fn(item.source_url, item.output_dir, use_archive=use_archive)
        if res.success:
            item.audio_path = res.audio_path
            if res.transcript_path:
                item.transcript_path = res.transcript_path
                # Map to .srt.txt for consistency if it's a CC
                if res.is_cc:
                    # We want to treat this CC as a finished transcription
                    # The pipeline expects .srt.txt and .txt
                    new_srt_txt = res.transcript_path.with_name(res.transcript_path.name + ".srt.txt")
                    res.transcript_path.rename(new_srt_txt)
                    item.transcript_path = new_srt_txt
                    
                    # Also create a plain .txt version
                    plain_txt = new_srt_txt.with_name(new_srt_txt.name.replace(".srt.txt", ".txt"))
                    # Minimal conversion from VTT/SRT to plain text if possible, 
                    # but for now just copying is fine, or let the pipeline handle it.
                    # Actually, if we just copy it, summarize_transcript might struggle with timestamps.
                    # But the pipeline's traditionalize stage expects .srt.txt.
                    shutil.copy(new_srt_txt, plain_txt)
                    
                    context.report_status(item_index, "ℹ️ 偵測到 YouTube 字幕，跳過語音下載與轉錄")

            item.download_ready = True
            if res.specific_url:
                item.source_url = res.specific_url
            if res.title:
                item.title = res.title

            # Logic to move to channel folder for Telegram ad-hoc tasks
            if getattr(context.args, "task_origin", "") == "telegram" and "telegram/youtube" in str(item.output_dir):
                self._move_to_channel_folder(item, context, item_index)

            detail = res.audio_path.name if res.audio_path else (res.transcript_path.name if res.transcript_path else "ok")
            context.log_event(item_index, "download", "ok", time.monotonic() - t_start, detail)
            return True
        
        if not use_archive:
            item.failed = True
            context.log_event(item_index, "download", "failed", time.monotonic() - t_start, "download failed or file not found")
        else:
            context.log_event(item_index, "download", "skipped", time.monotonic() - t_start, "no new video")
        return False

    def _move_to_channel_folder(self, item: DailyItem, context: PipelineContext, item_index: int) -> None:
        yt_dlp_bin = os.environ.get("YT_DLP_BIN", "yt-dlp")
        try:
            # 1. Fetch channel name
            res = subprocess.run([yt_dlp_bin, "--print", "%(uploader)s", item.source_url], capture_output=True, text=True, timeout=30)
            if res.returncode != 0:
                return
            channel_name = res.stdout.strip()
            if not channel_name:
                return
            
            # 2. Prepare new path
            safe_channel = sanitize_path_segment(channel_name, fallback="unknown-channel")
            # item.output_dir is typically output/telegram/youtube/{video_id}
            # We want output/telegram/youtube/{channel}/{video_id}
            parent = item.output_dir.parent
            new_output_dir = parent / safe_channel / item.output_dir.name
            
            if new_output_dir == item.output_dir:
                return

            # 3. Move files
            new_output_dir.parent.mkdir(parents=True, exist_ok=True)
            if item.output_dir.exists():
                # If target already exists (e.g. from another run), we might need to merge or just move
                if new_output_dir.exists():
                    # Move individual files instead of the directory
                    for f in item.output_dir.iterdir():
                        target_f = new_output_dir / f.name
                        if target_f.exists():
                            if target_f.is_file(): target_f.unlink()
                            else: shutil.rmtree(target_f)
                        shutil.move(str(f), str(new_output_dir))
                    item.output_dir.rmdir()
                else:
                    shutil.move(str(item.output_dir), str(new_output_dir))
                
                # Update item state
                old_audio = item.audio_path
                old_transcript = item.transcript_path
                item.output_dir = new_output_dir
                if old_audio:
                    item.audio_path = new_output_dir / old_audio.name
                if old_transcript:
                    item.transcript_path = new_output_dir / old_transcript.name
                
                update_task_metadata(item.output_dir, {"channel": channel_name})
                context.report_status(item_index, f"📂 已歸類至頻道：{channel_name}")
        except Exception as e:
            # Log error but don't fail the whole task
            context.report_status(item_index, f"⚠️ 無法歸類頻道資料夾: {e}", level="warning")


class PodcastDownloader(BaseDownloader):
    def __init__(self, sync_podcast_latest_fn: Callable[..., object]):
        self.sync_podcast_latest_fn = sync_podcast_latest_fn

    def can_handle(self, item: DailyItem) -> bool:
        return item.kind == "podcast"

    def download(self, item: DailyItem, context: PipelineContext, item_index: int) -> bool:
        t_start = time.monotonic()
        res = self.sync_podcast_latest_fn(
            item.source_url,
            item.output_dir,
            context.args.run_date,
            debug_mode=context.args.debug,
        )
        if res.skipped:
            if context.args.url:
                item.failed = True
                error_msg = f"❌ 找不到該日期的單集: {item.label}"
                item.messages.append(error_msg)
                context.log_event(item_index, "download", "failed", time.monotonic() - t_start, "no episode for specific request")
                return False
            context.log_event(item_index, "download", "skipped", time.monotonic() - t_start, "no episode")
            return False
        if res.success:
            item.audio_path = res.audio_path
            item.download_ready = True
            if res.specific_url:
                item.source_url = res.specific_url
            if res.title:
                item.title = res.title
            context.log_event(item_index, "download", "ok", time.monotonic() - t_start, res.audio_path.name)
            return True
        
        # If not skipped and not success, it's an error
        item.failed = True
        context.report_status(item_index, f"❌ 下載失敗：{item.label}", level="error")
        return False
