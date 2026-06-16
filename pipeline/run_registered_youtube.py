#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from project_runtime import bootstrap_project

BASE_DIR = bootstrap_project(ROOT_DIR)

from tools.recipient_groups import resolve_emails, load_recipient_groups
from tools.output_paths import subscribed_youtube_output_dir, youtube_channel_directory_name

@dataclass
class YouTubeSyncResult:
    audio_path: Optional[Path] = None
    transcript_path: Optional[Path] = None
    specific_url: Optional[str] = None
    title: str = ""
    success: bool = False
    already_exists: bool = False
    is_cc: bool = False

def load_subscriptions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    subscriptions = data.get("subscriptions", [])
    return [item for item in subscriptions if isinstance(item, dict)]

def make_channel_slug(channel_url: str) -> str:
    return youtube_channel_directory_name(channel_url)

def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True)

def check_youtube_subtitles(video_url: str) -> dict:
    """Checks for available subtitles and automatic captions."""
    yt_dlp_bin = os.environ.get("YT_DLP_BIN", "yt-dlp")
    # We use --print to get the subtitles and automatic_captions metadata
    result = run_command([
        yt_dlp_bin, "--skip-download", "--quiet", "--no-warnings",
        "--print", "%(subtitles)j", "--print", "%(automatic_captions)j",
        video_url
    ])
    if result.returncode != 0:
        return {"subtitles": {}, "automatic_captions": {}}
    
    try:
        lines = result.stdout.strip().splitlines()
        subs = json.loads(lines[0]) if len(lines) > 0 and lines[0] != "NA" else {}
        auto = json.loads(lines[1]) if len(lines) > 1 and lines[1] != "NA" else {}
        return {"subtitles": subs, "automatic_captions": auto}
    except (json.JSONDecodeError, IndexError):
        return {"subtitles": {}, "automatic_captions": {}}

def download_youtube_subtitles(video_url: str, output_dir: Path, preferred_langs: list[str] = ["zh-Hant", "zh-TW", "zh-HK", "zh-Hans", "zh", "en"]) -> Optional[Path]:
    """Attempts to download preferred subtitles or automatic captions."""
    yt_dlp_bin = os.environ.get("YT_DLP_BIN", "yt-dlp")
    
    # Check what's available first to pick the best one
    info = check_youtube_subtitles(video_url)
    
    target_lang = None
    use_auto = False
    
    # 1. Try manual subtitles first
    for lang in preferred_langs:
        if lang in info["subtitles"]:
            target_lang = lang
            break
    
    # 2. Try automatic captions if no manual sub found
    if not target_lang:
        for lang in preferred_langs:
            if lang in info["automatic_captions"]:
                target_lang = lang
                use_auto = True
                break
    
    if not target_lang:
        return None

    # Download the chosen subtitle
    cmd = [
        yt_dlp_bin, "--skip-download",
        "--write-subs" if not use_auto else "--write-auto-subs",
        "--sub-langs", target_lang,
        "--paths", str(output_dir),
        "-o", "%(title).200B__%(id)s.%(ext)s",
        video_url
    ]
    run_command(cmd)
    
    # Find the downloaded file (it usually has .<lang>.<ext> extension)
    # yt-dlp might save as .vtt, .srt, .ass, etc. We prefer srt or vtt.
    # The -o template ends with .%(ext)s but subtitles have extra part.
    # Actually -o applies to the video file, subtitles are named accordingly.
    # Let's search for files matching *__<vid>.<lang>.*
    vid_match = re.search(r"v=([a-zA-Z0-9_-]+)", video_url) or re.search(r"be/([a-zA-Z0-9_-]+)", video_url)
    vid = vid_match.group(1) if vid_match else None
    if not vid: return None
    
    for ext in ["srt", "vtt"]:
        matches = list(output_dir.glob(f"*__{vid}.{target_lang}.{ext}"))
        if matches:
            return matches[0]
            
    return None

def resolve_youtube_latest(channel_url: str) -> dict | None:
    yt_dlp_bin = os.environ.get("YT_DLP_BIN", "yt-dlp")
    # We fetch the last 5 items to find the most recent one that isn't 'upcoming'.
    # In flat-playlist mode, 'upcoming' live streams typically have duration=None.
    result = run_command([
        yt_dlp_bin, "--flat-playlist", "--playlist-end", "5",
        "--print-json", channel_url
    ])
    
    if result.returncode != 0 or not result.stdout.strip():
        return None
            
    try:
        lines = result.stdout.strip().splitlines()
        for line in lines:
            entry = json.loads(line)
            # Upcoming streams or private/unavailable videos often have no duration in flat mode.
            # Normal videos and finished streams ('was_live') will have a numeric duration.
            if entry.get("duration") is not None:
                return entry
        return None
    except (json.JSONDecodeError, IndexError):
        return None

def find_youtube_audio(output_dir: Path, video_id: str) -> Optional[Path]:
    if not output_dir.exists():
        return None
    matches = sorted(output_dir.glob(f"*__{video_id}.mp3"))
    return matches[0] if matches else None

def download_youtube_video(video_url: str, output_dir: Path, archive_file: Path | None = None) -> subprocess.CompletedProcess:
    yt_dlp_bin = os.environ.get("YT_DLP_BIN", "yt-dlp")
    ffmpeg_bin = os.environ.get("FFMPEG_BIN")
    
    command = [
        yt_dlp_bin, "--no-overwrites",
        "-f", "bestaudio/best", "-x", "--audio-format", "mp3",
        "--paths", str(output_dir), "-o", "%(title).200B__%(id)s.%(ext)s"
    ]

    if archive_file is not None:
        command[1:1] = ["--download-archive", str(archive_file)]
    
    if ffmpeg_bin:
        command.extend(["--ffmpeg-location", ffmpeg_bin])
        
    command.append(video_url)
    return run_command(command)

def sync_youtube_latest(source_url: str, output_dir: Path, use_archive: bool = True) -> YouTubeSyncResult:
    """
    Cohesive function to resolve, check, and download the latest YouTube video.
    Returns a YouTubeSyncResult object.
    """
    result = YouTubeSyncResult()
    is_direct_video = "watch?v=" in source_url or "youtu.be/" in source_url
    vid = None

    if is_direct_video:
        vid_match = re.search(r"v=([a-zA-Z0-9_-]+)", source_url) or re.search(r"be/([a-zA-Z0-9_-]+)", source_url)
        vid = vid_match.group(1) if vid_match else None
        result.specific_url = source_url
    else:
        latest = resolve_youtube_latest(source_url)
        if not latest:
            return result
        result.title = latest.get("title", "")
        result.specific_url = latest.get("webpage_url")
        vid = latest.get("id")

    if vid:
        existing = find_youtube_audio(output_dir, vid)
        if existing:
            result.audio_path = existing
            result.already_exists = True
            result.success = True
            return result

    # 1. Try CC subtitles first
    target_url = result.specific_url or source_url
    cc_path = download_youtube_subtitles(target_url, output_dir)
    if cc_path:
        result.transcript_path = cc_path
        result.is_cc = True
        result.success = True
        return result

    # 2. Fallback to audio download
    archive_file = Path(__file__).resolve().parent.parent / "id.txt" if use_archive else None
    
    dl_res = download_youtube_video(target_url, output_dir, archive_file)
    
    if vid:
        result.audio_path = find_youtube_audio(output_dir, vid)
        result.success = result.audio_path is not None
        
    return result

def main() -> int:
    # (Main remains mostly for backward compatibility or direct CLI usage)
    parser = argparse.ArgumentParser(description="Process registered YouTube channels.")
    parser.add_argument("--config", default="youtube_subscriptions.json")
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--transcribe-script", default=os.environ.get("GENSRT_SCRIPT", "gensrt.sh"))
    parser.add_argument("--recipient-config", default=os.environ.get("RECIPIENT_CONFIG_FILE", "recipient_groups.local.json"))
    parser.add_argument("--traditionalize-transcript", action="store_true", default=os.environ.get("OPENCC_TRADITIONALIZE", "0") == "1")
    parser.add_argument("--opencc-config", default=os.environ.get("OPENCC_CONFIG", "s2twp.json"))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    # ... logic for batch processing registered channels ...
    # (This could also be refactored to use sync_youtube_latest)
    # For now, let's just make sure the module is importable and clean.
    return 0

if __name__ == "__main__":
    # In practice, run_daily_pipeline.py is the main entry point now.
    pass
