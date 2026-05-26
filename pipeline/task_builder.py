from __future__ import annotations

from pathlib import Path
from typing import List

from project_runtime import resolve_project_path
from pipeline.models import DailyItem
from pipeline.run_registered_podcasts import load_subscriptions as load_podcast_subs
from pipeline.run_registered_podcasts import resolve_podcast_title
from pipeline.run_registered_youtube import load_subscriptions as load_youtube_subs
from tools.output_paths import (
    infer_podcast_slug,
    subscribed_podcast_output_dir,
    subscribed_youtube_output_dir,
    telegram_url_output_dir,
    youtube_video_id,
)
from tools.recipient_groups import load_recipient_groups, resolve_emails


class TaskBuilder:
    def __init__(
        self,
        args,
        root: Path,
        base_dir: Path,
        *,
        load_recipient_groups_fn=load_recipient_groups,
        resolve_emails_fn=resolve_emails,
        load_podcast_subs_fn=load_podcast_subs,
        load_youtube_subs_fn=load_youtube_subs,
        resolve_podcast_title_fn=resolve_podcast_title,
    ):
        self.args = args
        self.root = root
        self.base_dir = base_dir
        self.load_recipient_groups_fn = load_recipient_groups_fn
        self.resolve_emails_fn = resolve_emails_fn
        self.load_podcast_subs_fn = load_podcast_subs_fn
        self.load_youtube_subs_fn = load_youtube_subs_fn
        self.resolve_podcast_title_fn = resolve_podcast_title_fn
        self.groups = self.load_recipient_groups_fn(resolve_project_path(base_dir, args.recipient_config))

    def build(self) -> List[DailyItem]:
        if self.args.url:
            return [self._build_url_item()]
        if getattr(self.args, "local_file", None):
            return [self._build_local_file_item()]
        return self._build_subscription_items()

    def _build_url_item(self) -> DailyItem:
        emails = self.resolve_emails_fn({"recipient_group": self.args.recipient_group}, self.groups)
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
        emails = self.resolve_emails_fn({"recipient_group": self.args.recipient_group}, self.groups)
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
        pod_cfg = resolve_project_path(self.base_dir, self.args.podcast_config)
        yt_cfg = resolve_project_path(self.base_dir, self.args.youtube_config)
        for index, sub in enumerate(self.load_podcast_subs_fn(pod_cfg), 1):
            rss = sub.get("rss_url", "").strip()
            title = sub.get("podcast_title", "").strip() or (self.resolve_podcast_title_fn(rss) if rss else "")
            prompt_file = self.base_dir / sub["prompt_file"] if sub.get("prompt_file") else None
            items.append(
                DailyItem(
                    f"Podcast {index}",
                    "podcast",
                    sub.get("podcast_url", rss),
                    self.resolve_emails_fn(sub, self.groups),
                    subscribed_podcast_output_dir(self.root, title or "podcast"),
                    prompt_file=prompt_file,
                    title=title,
                )
            )
        for index, sub in enumerate(self.load_youtube_subs_fn(yt_cfg), 1):
            url = sub.get("channel_url", "").strip()
            prompt_file = self.base_dir / sub["prompt_file"] if sub.get("prompt_file") else None
            items.append(
                DailyItem(
                    f"YouTube {index}" if index > 1 else "YouTube",
                    "youtube",
                    url,
                    self.resolve_emails_fn(sub, self.groups),
                    subscribed_youtube_output_dir(self.root, url),
                    prompt_file=prompt_file,
                )
            )
        return items
