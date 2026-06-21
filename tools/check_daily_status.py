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
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from pipeline.run_registered_podcasts import load_subscriptions as load_pod_subs
from pipeline.run_registered_youtube import load_subscriptions as load_yt_subs
from tools.output_paths import subscribed_podcast_output_dir, subscribed_youtube_output_dir

def format_size(size_bytes: int) -> str:
    """Formats bytes into human-readable string."""
    if size_bytes < 1024: return f"{size_bytes} B"
    elif size_bytes < 1024**2: return f"{size_bytes/1024:.1f} KB"
    elif size_bytes < 1024**3: return f"{size_bytes/1024**2:.1f} MB"
    else: return f"{size_bytes/1024**3:.2f} GB"

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

def render_pipeline(stages: dict, last_update: str, size: int = 0) -> str:
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

    size_str = f" | {format_size(size)}" if size > 0 else ""
    return " ⮕ ".join(parts) + f"  ({last_update}{size_str})"

def get_task_title(filename: str) -> str:
    """Extracts a clean title from filenames, removing all known suffixes and hashes."""
    # Strip hashes and various sent/mail markers
    name = re.sub(r'\.[a-f0-9]{12}\.(mail-)?sent$', '', filename)
    name = re.sub(r'\.[a-f0-9]{12}\.mail\.txt$', '', name)
    
    suffixes = [
        ".zh-Hant.summary.md", ".summary.md",
        ".zh-Hant.srt.txt", ".srt.txt",
        ".zh-Hant.txt", ".txt",
        ".mp3", ".wav", ".m4a", ".webm", ".mp4", ".mov",
        ".json", ".srt"
    ]
    for s in sorted(suffixes, key=len, reverse=True):
        if name.endswith(s):
            name = name[:-len(s)]
            break
    return name

def process_directory(dir_path: Path, active_wavs: list, today: str, today_dots: str) -> dict:
    """Groups files in a directory into tasks and identifies their pipeline stages."""
    tasks = defaultdict(lambda: {"stages": {}, "mtime": 0, "title": "", "size": 0})
    if not dir_path.exists():
        return tasks

    dir_str = str(dir_path.resolve())
    
    for wav in active_wavs:
        if dir_str in wav:
            title = get_task_title(os.path.basename(wav))
            tasks[title]["stages"]["transcribing"] = True
            tasks[title]["title"] = title

    for f in dir_path.rglob("*"):
        if not f.is_file() or f.name == ".DS_Store": continue
        
        st = f.stat()
        mtime_dt = datetime.fromtimestamp(st.st_mtime)
        mtime_date = mtime_dt.date().isoformat()
        
        if mtime_date == today or today in f.name or today_dots in f.name:
            title = get_task_title(f.name)
            if not title or title == "metadata": continue
            
            tasks[title]["title"] = title
            tasks[title]["size"] += st.st_size
            if st.st_mtime > tasks[title]["mtime"]:
                tasks[title]["mtime"] = st.st_mtime
            
            if f.suffix in [".mp3", ".wav", ".m4a", ".webm", ".mp4", ".mov"]:
                tasks[title]["stages"]["download"] = True
            elif f.name.endswith(".srt.txt") or f.name.endswith(".zh-Hant.txt") or f.name.endswith(".srt"):
                tasks[title]["stages"]["transcribe"] = True
            elif f.name.endswith(".summary.md"):
                tasks[title]["stages"]["summarize"] = True
            elif ".sent" in f.name:
                tasks[title]["stages"]["mail"] = True
                
    return tasks

def get_overall_stats(root: Path):
    """Calculates total count and size of all tasks in the output directory."""
    total_size = 0
    task_ids = set()
    for f in root.rglob("*"):
        if f.is_file() and f.name != ".DS_Store":
            total_size += f.stat().st_size
            title = get_task_title(f.name)
            if title and title != "metadata":
                # Use parent dir + title to distinguish tasks across different sources
                task_ids.add(f"{f.parent.name}/{title}")
    return len(task_ids), total_size

def check_status():
    now = datetime.now()
    today = now.date().isoformat()
    today_dots = today.replace("-", "⧸")
    active_wavs = get_active_transcriptions()
    
    output_root = BASE_DIR / "output"
    pod_cfg = BASE_DIR / "config" / "subscriptions.json"
    yt_cfg = BASE_DIR / "config" / "youtube_subscriptions.json"
    
    # Pre-collect data to show top summary
    all_today_tasks = []
    
    # 1. Podcasts
    pod_results = []
    if pod_cfg.exists():
        for sub in load_pod_subs(pod_cfg):
            title = sub.get("podcast_title", "Unknown Podcast")
            out_dir = subscribed_podcast_output_dir(output_root, title)
            tasks = process_directory(out_dir, active_wavs, today, today_dots)
            pod_results.append((title, tasks))
            all_today_tasks.extend(tasks.values())

    # 2. YouTube
    yt_results = []
    if yt_cfg.exists():
        for sub in load_yt_subs(yt_cfg):
            url = sub.get("channel_url", "")
            out_dir = subscribed_youtube_output_dir(output_root, url)
            tasks = process_directory(out_dir, active_wavs, today, today_dots)
            yt_results.append((out_dir.name, tasks))
            all_today_tasks.extend(tasks.values())

    # 3. Telegram
    tg_results = []
    tg_root = output_root / "telegram"
    if tg_root.exists():
        for task_type in ["audio", "video", "youtube", "apple_podcast", "soundon_podcast"]:
            type_dir = tg_root / task_type
            if not type_dir.exists(): continue
            for task_dir in sorted(type_dir.iterdir(), key=os.path.getmtime, reverse=True):
                if not task_dir.is_dir() or task_dir.name == "reports": continue
                st_dir = task_dir.stat()
                if datetime.fromtimestamp(st_dir.st_mtime).date().isoformat() != today and not task_dir.name.startswith(today.replace("-", "")):
                    continue
                tasks = process_directory(task_dir, active_wavs, today, today_dots)
                if tasks:
                    tg_results.append((f"{task_type}/{task_dir.name}", tasks))
                    all_today_tasks.extend(tasks.values())

    # --- Start Printing ---
    print(f"--- Daily Status Check ({today} {now.strftime('%H:%M:%S')}) ---")
    
    # Top Summary (Scheme A component)
    today_size = sum(t["size"] for t in all_today_tasks)
    print(f"📊 今日統計：{len(all_today_tasks)} 筆任務 | 總計 {format_size(today_size)}")

    # 1. Podcasts (Scheme B headers)
    for title, tasks in pod_results:
        count = len(tasks)
        size = sum(t["size"] for t in tasks.values())
        print(f"\n[Podcast] {title} ({count} 筆, {format_size(size)})")
        if not tasks: print("  Status: No activity today")
        else:
            for task_id in sorted(tasks, key=lambda x: tasks[x]["mtime"], reverse=True):
                t = tasks[task_id]
                update_time = datetime.fromtimestamp(t["mtime"]).strftime("%H:%M:%S") if t["mtime"] > 0 else "Active"
                print(f"  - {t['title']}")
                print(f"    {render_pipeline(t['stages'], update_time, t['size'])}")

    # 2. YouTube
    for name, tasks in yt_results:
        count = len(tasks)
        size = sum(t["size"] for t in tasks.values())
        print(f"\n[YouTube] {name} ({count} 筆, {format_size(size)})")
        if not tasks: print("  Status: No activity today")
        else:
            for task_id in sorted(tasks, key=lambda x: tasks[x]["mtime"], reverse=True):
                t = tasks[task_id]
                update_time = datetime.fromtimestamp(t["mtime"]).strftime("%H:%M:%S") if t["mtime"] > 0 else "Active"
                print(f"  - {t['title']}")
                print(f"    {render_pipeline(t['stages'], update_time, t['size'])}")

    # 3. Telegram
    if tg_results:
        tg_total_count = sum(len(r[1]) for r in tg_results)
        tg_total_size = sum(sum(t["size"] for t in r[1].values()) for r in tg_results)
        print(f"\n[Telegram Tasks] ({tg_total_count} 筆, {format_size(tg_total_size)})")
        for label, tasks in tg_results:
            if not tasks:
                print(f"  - Task: {label}")
                print(f"    [ ⏳ 等待中... ]")
            else:
                for task_id in tasks:
                    t = tasks[task_id]
                    update_time = datetime.fromtimestamp(t["mtime"]).strftime("%H:%M:%S") if t["mtime"] > 0 else "Active"
                    print(f"  - Task: {label} ({t['title']})")
                    print(f"    {render_pipeline(t['stages'], update_time, t['size'])}")
    elif tg_root.exists():
        print(f"\n[Telegram Tasks]")
        print("  Status: No activity today")

    # Footer Summary (Scheme C)
    print("\n" + "-"*40)
    total_count, total_size_bytes = get_overall_stats(output_root)
    print(f"📁 目前 Output 總存量：{total_count} 筆任務 | {format_size(total_size_bytes)}")

if __name__ == "__main__":
    check_status()
