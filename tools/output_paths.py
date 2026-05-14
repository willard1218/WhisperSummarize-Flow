#!/usr/bin/env python3

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


def sanitize_path_segment(value: str, fallback: str = "item") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    cleaned = cleaned.replace("/", " ").replace("\\", " ")
    return cleaned or fallback


def slugify_segment(value: str, fallback: str = "item") -> str:
    cleaned = sanitize_path_segment(value, fallback=fallback)
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", cleaned).strip("-")
    return slug or fallback


def youtube_channel_directory_name(channel_url: str) -> str:
    parsed = urlparse(channel_url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if segments and segments[0].startswith("@"):
        return sanitize_path_segment(segments[0][1:], fallback="youtube-channel")
    if segments:
        return sanitize_path_segment(segments[0], fallback="youtube-channel")
    return "youtube-channel"


def podcast_directory_name(podcast_title: str, fallback: str = "podcast") -> str:
    return sanitize_path_segment(podcast_title, fallback=fallback)


def output_root_for_subscription(base_output_root: Path, kind: str) -> Path:
    return base_output_root / kind


def subscribed_podcast_output_dir(base_output_root: Path, podcast_title: str) -> Path:
    return output_root_for_subscription(base_output_root, "podcast") / podcast_directory_name(podcast_title)


def subscribed_youtube_output_dir(base_output_root: Path, channel_url: str) -> Path:
    return output_root_for_subscription(base_output_root, "youtube") / youtube_channel_directory_name(channel_url)


def youtube_video_id(url: str) -> str | None:
    watch_match = re.search(r"[?&]v=([a-zA-Z0-9_-]+)", url)
    if watch_match:
        return watch_match.group(1)
    short_match = re.search(r"youtu\.be/([a-zA-Z0-9_-]+)", url)
    if short_match:
        return short_match.group(1)
    return None


def infer_podcast_slug(url: str) -> str:
    parsed = urlparse(url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if segments:
        return slugify_segment(segments[-1], fallback="podcast")
    return "podcast"


def telegram_url_output_dir(base_output_root: Path, url: str) -> Path:
    if "youtube.com" in url or "youtu.be" in url:
        return base_output_root / "telegram" / "youtube" / (youtube_video_id(url) or "unknown-video")
    return base_output_root / "telegram" / "podcast" / infer_podcast_slug(url)


def telegram_media_output_dir(base_output_root: Path, media_kind: str, original_name: str, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    bucket = "video" if media_kind == "video" else "audio"
    stem = Path(original_name).stem if original_name else media_kind
    slug = slugify_segment(stem, fallback=media_kind)
    return base_output_root / "telegram" / bucket / f"{timestamp}_{slug}"


def write_task_metadata(directory: Path, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    metadata_path = directory / "metadata.json"
    metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata_path
