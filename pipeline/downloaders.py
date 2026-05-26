from __future__ import annotations

import time
from typing import Callable

from pipeline.models import DailyItem, PipelineContext


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
        res = self.sync_youtube_latest_fn(item.source_url, item.output_dir, use_archive=use_archive)
        if res.success:
            item.audio_path = res.audio_path
            item.download_ready = True
            if res.specific_url:
                item.source_url = res.specific_url
            if res.title:
                item.title = res.title
            detail = res.audio_path.name if res.audio_path else "ok"
            context.log_event(item_index, "download", "ok", time.monotonic() - t_start, detail)
            return True
        context.log_event(item_index, "download", "skipped", time.monotonic() - t_start, "no new video")
        return False


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
        return False
