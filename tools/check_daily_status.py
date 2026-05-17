#!/usr/bin/env python3

import os
import sys
import subprocess
from pathlib import Path
from datetime import date, datetime

# Setup paths
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "tools"))
sys.path.insert(0, str(BASE_DIR / "pipeline"))

from run_registered_podcasts import load_subscriptions as load_pod_subs
from run_registered_youtube import load_subscriptions as load_yt_subs
from output_paths import subscribed_podcast_output_dir, subscribed_youtube_output_dir

def get_file_status(f: Path):
    if f.name.endswith(".mail-sent"): return "📧 Sent"
    if f.name.endswith(".summary.md"): return "🤖 Summarized"
    if f.name.endswith(".zh-Hant.txt"): return "🔠 Transcribed (Hant)"
    if f.name.endswith(".srt.txt"): return "🎙️ Transcribed"
    if f.suffix in [".mp3", ".wav", ".m4a", ".webm", ".mp4", ".mov"]: return "📂 Downloaded"
    return None

def get_active_transcriptions():
    """Returns a list of audio file paths currently being processed by whisperkit-cli."""
    try:
        # Use 'wwaux' for macOS to get full command lines
        output = subprocess.check_output(["ps", "wwaux"], text=True)
        active = []
        for line in output.splitlines():
            if "whisperkit-cli" in line and "transcribe" in line and "check_daily_status.py" not in line:
                if "--audio-path" in line:
                    part = line.split("--audio-path", 1)[1].strip()
                    if " --" in part:
                        path = part.split(" --", 1)[0].strip()
                    else:
                        path = part.strip()
                    active.append(path)
        return active
    except Exception as e:
        print(f"DEBUG: Error in get_active_transcriptions: {e}")
        return []

def check_status():
    today = date.today().isoformat()
    today_dots = today.replace("-", "⧸")
    active_wavs = get_active_transcriptions()
    
    output_root = BASE_DIR / "output"
    pod_cfg = BASE_DIR / "config" / "subscriptions.json"
    yt_cfg = BASE_DIR / "config" / "youtube_subscriptions.json"
    
    print(f"--- Daily Status Check ({today}) ---")
    
    # 1. Podcasts
    if pod_cfg.exists():
        for sub in load_pod_subs(pod_cfg):
            title = sub.get("podcast_title", "Unknown Podcast")
            out_dir = subscribed_podcast_output_dir(output_root, title)
            out_dir_str = str(out_dir.resolve())
            
            print(f"\n[Podcast] {title}")
            if not out_dir.exists():
                print("  Status: No activity today (Folder not found)")
                continue
            
            files = sorted(out_dir.rglob("*"), key=os.path.getmtime, reverse=True)
            found_any = False
            seen_stems = set()
            
            # Check if any wav in this dir is being active
            for wav in active_wavs:
                if out_dir_str in wav:
                    print(f"  - 🎙️ Transcribing: {os.path.basename(wav)}...")
                    found_any = True

            for f in files:
                if not f.is_file(): continue
                mtime = date.fromtimestamp(f.stat().st_mtime).isoformat()
                if mtime == today or today in f.name or today_dots in f.name:
                    found_any = True
                    status = get_file_status(f)
                    if status:
                        stem = f.name.split(".")[0]
                        key = f"{stem}_{status}"
                        if key not in seen_stems:
                            print(f"  - {status}: {f.name}")
                            seen_stems.add(key)
            
            if not found_any:
                print("  Status: No activity today")

    # 2. YouTube
    if yt_cfg.exists():
        for sub in load_yt_subs(yt_cfg):
            url = sub.get("channel_url", "")
            out_dir = subscribed_youtube_output_dir(output_root, url)
            out_dir_str = str(out_dir.resolve())
            
            print(f"\n[YouTube] {out_dir.name}")
            if not out_dir.exists():
                print("  Status: No activity today (Folder not found)")
                continue
                
            files = sorted(out_dir.rglob("*"), key=os.path.getmtime, reverse=True)
            found_any = False
            seen_stems = set()

            for wav in active_wavs:
                if out_dir_str in wav:
                    print(f"  - 🎙️ Transcribing: {os.path.basename(wav)}...")
                    found_any = True

            for f in files:
                if not f.is_file(): continue
                mtime = date.fromtimestamp(f.stat().st_mtime).isoformat()
                if mtime == today or today in f.name or today_dots in f.name:
                    found_any = True
                    status = get_file_status(f)
                    if status:
                        stem = f.name.split(".")[0]
                        key = f"{stem}_{status}"
                        if key not in seen_stems:
                            print(f"  - {status}: {f.name}")
                            seen_stems.add(key)
            
            if not found_any:
                print("  Status: No activity today")

    # 3. Telegram Tasks
    tg_root = output_root / "telegram"
    if tg_root.exists():
        print(f"\n[Telegram Tasks]")
        found_any = False
        # Scan subdirectories for today's tasks
        for task_type in ["audio", "video", "youtube", "podcast"]:
            type_dir = tg_root / task_type
            if not type_dir.exists(): continue
            
            for task_dir in sorted(type_dir.iterdir(), key=os.path.getmtime, reverse=True):
                if not task_dir.is_dir(): continue
                
                # Check folder name (starts with date for media) or mtime
                mtime = date.fromtimestamp(task_dir.stat().st_mtime).isoformat()
                if mtime == today or task_dir.name.startswith(today.replace("-", "")):
                    found_any = True
                    
                    # Check for active transcription in telegram task
                    task_out_dir_str = str(task_dir.resolve())
                    for wav in active_wavs:
                        if task_out_dir_str in wav:
                            print(f"  - Task: {task_type}/{task_dir.name} [🎙️ Transcribing]")
                            found_any = True
                    else:
                        print(f"  - Task: {task_type}/{task_dir.name}")

                    seen_stems = set()
                    for f in sorted(task_dir.glob("*"), key=os.path.getmtime, reverse=True):
                        status = get_file_status(f)
                        if status:
                            stem = f.name.split(".")[0]
                            key = f"{stem}_{status}"
                            if key not in seen_stems:
                                print(f"    * {status}: {f.name}")
                                seen_stems.add(key)
        
        if not found_any:
            print("  Status: No activity today")

if __name__ == "__main__":
    check_status()
