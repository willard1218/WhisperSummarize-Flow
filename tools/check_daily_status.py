#!/usr/bin/env python3

import os
import sys
import re
import subprocess
from pathlib import Path
from datetime import date, datetime
from collections import defaultdict

# Setup paths
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "tools"))
sys.path.insert(0, str(BASE_DIR / "pipeline"))

from run_registered_podcasts import load_subscriptions as load_pod_subs
from run_registered_youtube import load_subscriptions as load_yt_subs
from output_paths import subscribed_podcast_output_dir, subscribed_youtube_output_dir

def get_active_transcriptions():
    """Returns a list of audio file paths currently being processed by whisperkit-cli."""
    try:
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
    except Exception:
        return []

def render_pipeline(stages: dict, last_update: str) -> str:
    """Renders a visual pipeline string based on completed stages."""
    order = [
        ("download", "下載", "📂"),
        ("transcribe", "轉錄", "🎙️"),
        ("summarize", "摘要", "🤖"),
        ("mail", "寄出", "📧")
    ]
    
    parts = []
    for key, label, emoji in order:
        if stages.get(key):
            parts.append(f"[{emoji}{label}]")
        elif key == "transcribe" and stages.get("transcribing"):
            parts.append("[⏳轉錄中]")
        else:
            parts.append(f"[  {label}  ]")

    return " ⮕ ".join(parts) + f"  ({last_update})"

def get_task_title(filename: str) -> str:
    """Extracts a clean title from filenames, removing all known suffixes and hashes."""
    # 1. Remove mail/sent markers with hashes (e.g., .7d5f9c0cbead.sent)
    name = re.sub(r'\.[a-f0-9]{12}\.(mail-)?sent$', '', filename)
    
    # 2. Remove other known suffixes
    suffixes = [
        ".zh-Hant.summary.md", ".summary.md",
        ".zh-Hant.srt.txt", ".srt.txt",
        ".zh-Hant.txt", ".txt",
        ".mp3", ".wav", ".m4a", ".webm", ".mp4", ".mov",
        ".json", ".srt"
    ]
    # Match longest suffixes first
    for s in sorted(suffixes, key=len, reverse=True):
        if name.endswith(s):
            name = name[:-len(s)]
            break
    return name

def process_directory(dir_path: Path, active_wavs: list, today: str, today_dots: str) -> dict:
    """Groups files in a directory into tasks and identifies their pipeline stages."""
    tasks = defaultdict(lambda: {"stages": {}, "mtime": 0, "title": ""})
    if not dir_path.exists():
        return tasks

    dir_str = str(dir_path.resolve())
    
    # Identify active transcriptions in this directory
    for wav in active_wavs:
        if dir_str in wav:
            title = get_task_title(os.path.basename(wav))
            tasks[title]["stages"]["transcribing"] = True
            tasks[title]["title"] = title

    for f in dir_path.rglob("*"):
        if not f.is_file(): continue
        if f.name == ".DS_Store": continue
        
        st = f.stat()
        mtime_dt = datetime.fromtimestamp(st.st_mtime)
        mtime_date = mtime_dt.date().isoformat()
        
        # Check if file belongs to today
        if mtime_date == today or today in f.name or today_dots in f.name:
            title = get_task_title(f.name)
            if not title or title == "metadata": continue
            
            tasks[title]["title"] = title
            if st.st_mtime > tasks[title]["mtime"]:
                tasks[title]["mtime"] = st.st_mtime
            
            # Map file type to stage
            if f.suffix in [".mp3", ".wav", ".m4a", ".webm", ".mp4", ".mov"]:
                tasks[title]["stages"]["download"] = True
            elif f.name.endswith(".srt.txt") or f.name.endswith(".zh-Hant.txt") or f.name.endswith(".srt"):
                tasks[title]["stages"]["transcribe"] = True
            elif f.name.endswith(".summary.md"):
                tasks[title]["stages"]["summarize"] = True
            elif ".sent" in f.name:
                tasks[title]["stages"]["mail"] = True
                
    return tasks

def check_status():
    now = datetime.now()
    today = now.date().isoformat()
    today_dots = today.replace("-", "⧸")
    active_wavs = get_active_transcriptions()
    
    output_root = BASE_DIR / "output"
    pod_cfg = BASE_DIR / "config" / "subscriptions.json"
    yt_cfg = BASE_DIR / "config" / "youtube_subscriptions.json"
    
    print(f"--- Daily Status Check ({today} {now.strftime('%H:%M:%S')}) ---")
    
    # 1. Podcasts
    if pod_cfg.exists():
        for sub in load_pod_subs(pod_cfg):
            title = sub.get("podcast_title", "Unknown Podcast")
            out_dir = subscribed_podcast_output_dir(output_root, title)
            print(f"\n[Podcast] {title}")
            
            tasks = process_directory(out_dir, active_wavs, today, today_dots)
            if not tasks:
                print("  Status: No activity today")
            else:
                for task_id in sorted(tasks, key=lambda x: tasks[x]["mtime"], reverse=True):
                    t = tasks[task_id]
                    update_time = datetime.fromtimestamp(t["mtime"]).strftime("%H:%M:%S") if t["mtime"] > 0 else "Active"
                    print(f"  - {t['title']}")
                    print(f"    {render_pipeline(t['stages'], update_time)}")

    # 2. YouTube
    if yt_cfg.exists():
        for sub in load_yt_subs(yt_cfg):
            url = sub.get("channel_url", "")
            out_dir = subscribed_youtube_output_dir(output_root, url)
            print(f"\n[YouTube] {out_dir.name}")
            
            tasks = process_directory(out_dir, active_wavs, today, today_dots)
            if not tasks:
                print("  Status: No activity today")
            else:
                for task_id in sorted(tasks, key=lambda x: tasks[x]["mtime"], reverse=True):
                    t = tasks[task_id]
                    update_time = datetime.fromtimestamp(t["mtime"]).strftime("%H:%M:%S") if t["mtime"] > 0 else "Active"
                    print(f"  - {t['title']}")
                    print(f"    {render_pipeline(t['stages'], update_time)}")

    # 3. Telegram Tasks
    tg_root = output_root / "telegram"
    if tg_root.exists():
        print(f"\n[Telegram Tasks]")
        found_any = False
        for task_type in ["audio", "video", "youtube", "apple_podcast", "soundon_podcast"]:
            type_dir = tg_root / task_type
            if not type_dir.exists(): continue
            
            for task_dir in sorted(type_dir.iterdir(), key=os.path.getmtime, reverse=True):
                if not task_dir.is_dir() or task_dir.name == "reports": continue
                
                st_dir = task_dir.stat()
                mtime_dt_dir = datetime.fromtimestamp(st_dir.st_mtime)
                if mtime_dt_dir.date().isoformat() != today and not task_dir.name.startswith(today.replace("-", "")):
                    continue
                
                found_any = True
                tasks = process_directory(task_dir, active_wavs, today, today_dots)
                
                if not tasks:
                    print(f"  - Task: {task_type}/{task_dir.name}")
                    print(f"    [ ⏳ 等待中... ]")
                else:
                    for task_id in tasks:
                        t = tasks[task_id]
                        update_time = datetime.fromtimestamp(t["mtime"]).strftime("%H:%M:%S") if t["mtime"] > 0 else "Active"
                        print(f"  - Task: {task_type}/{task_dir.name} ({t['title']})")
                        print(f"    {render_pipeline(t['stages'], update_time)}")
        
        if not found_any:
            print("  Status: No activity today")

if __name__ == "__main__":
    check_status()
