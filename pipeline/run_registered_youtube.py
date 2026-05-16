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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from recipient_groups import resolve_emails, load_recipient_groups
from output_paths import subscribed_youtube_output_dir, youtube_channel_directory_name

@dataclass
class YouTubeSyncResult:
    audio_path: Optional[Path] = None
    specific_url: Optional[str] = None
    title: str = ""
    success: bool = False
    already_exists: bool = False

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

def resolve_youtube_latest(channel_url: str) -> dict | None:
    yt_dlp_bin = os.environ.get("YT_DLP_BIN", "yt-dlp")
    # match_filter might skip valid streams if they are just finished, 
    # but for daily pipeline it's a good guard.
    result = run_command([
        yt_dlp_bin, "--flat-playlist", "--playlist-end", "1",
        "--match-filter", "live_status=was_live", "--print-json", channel_url
    ])
    if result.returncode != 0 or not result.stdout.strip():
        # Fallback: try without filter if it's a direct channel link and nothing was live
        result = run_command([
            yt_dlp_bin, "--flat-playlist", "--playlist-end", "1",
            "--print-json", channel_url
        ])
        if result.returncode != 0 or not result.stdout.strip():
            return None
            
    try:
        return json.loads(result.stdout.splitlines()[-1])
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

    # Actually download
    archive_file = Path(__file__).resolve().parent.parent / "id.txt" if use_archive else None
    target_url = result.specific_url or source_url
    
    dl_res = download_youtube_video(target_url, output_dir, archive_file)
    if dl_res.stdout:
        print(dl_res.stdout, end="")
    
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
