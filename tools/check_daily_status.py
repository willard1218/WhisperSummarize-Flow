#!/usr/bin/env python3

import os
import sys
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

def check_status():
    today = date.today().isoformat()
    today_dots = today.replace("-", "⧸")
    
    output_root = BASE_DIR / "output"
    pod_cfg = BASE_DIR / "config" / "subscriptions.json"
    yt_cfg = BASE_DIR / "config" / "youtube_subscriptions.json"
    
    print(f"--- Daily Status Check ({today}) ---")
    
    # 1. Podcasts
    if pod_cfg.exists():
        for sub in load_pod_subs(pod_cfg):
            title = sub.get("podcast_title", "Unknown Podcast")
            out_dir = subscribed_podcast_output_dir(output_root, title)
            
            print(f"\n[Podcast] {title}")
            if not out_dir.exists():
                print("  Status: No activity today (Folder not found)")
                continue
            
            files = sorted(out_dir.rglob("*"), key=os.path.getmtime, reverse=True)
            found_any = False
            seen_stems = set()
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
                            print(f"  - {status}: {f.name[:60]}...")
                            seen_stems.add(key)
            
            if not found_any:
                print("  Status: No activity today")

    # 2. YouTube
    if yt_cfg.exists():
        for sub in load_yt_subs(yt_cfg):
            url = sub.get("channel_url", "")
            out_dir = subscribed_youtube_output_dir(output_root, url)
            
            print(f"\n[YouTube] {out_dir.name}")
            if not out_dir.exists():
                print("  Status: No activity today (Folder not found)")
                continue
                
            files = sorted(out_dir.rglob("*"), key=os.path.getmtime, reverse=True)
            found_any = False
            seen_stems = set()
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
                            print(f"  - {status}: {f.name[:60]}...")
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
                    print(f"  - Task: {task_type}/{task_dir.name}")
                    seen_stems = set()
                    for f in sorted(task_dir.glob("*"), key=os.path.getmtime, reverse=True):
                        status = get_file_status(f)
                        if status:
                            stem = f.name.split(".")[0]
                            key = f"{stem}_{status}"
                            if key not in seen_stems:
                                print(f"    * {status}: {f.name[:40]}...")
                                seen_stems.add(key)
        
        if not found_any:
            print("  Status: No activity today")

if __name__ == "__main__":
    check_status()
