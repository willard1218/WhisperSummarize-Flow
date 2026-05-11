#!/usr/bin/env python3

import os
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Setup paths
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# --- Configuration Loading ---

def load_local_config():
    config_path = BASE_DIR / "config" / "local_config.sh"
    if config_path.exists():
        content = config_path.read_text()
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"): continue
            
            # Remove 'export ' prefix if present
            PREFIX = "export "
            if line.startswith(PREFIX):
                line = line[len(PREFIX):].strip()
            
            if "=" in line:
                try:
                    # Split only on first '=' and remove comments
                    kv_part = line.split("#", 1)[0].strip()
                    key, value = kv_part.split("=", 1)
                    value = value.strip().strip('"').strip("'")
                    
                    # Handle PATH variable specifically
                    if key == "PATH":
                        os.environ["PATH"] = value.replace("$PATH", os.environ.get("PATH", ""))
                    else:
                        os.environ[key] = value
                except ValueError:
                    continue

# Initialize config
load_local_config()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OWNER_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not BOT_TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN not found in local_config.sh")
    sys.exit(1)

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/"

# --- Telegram API Helpers ---

def call_api(method, data=None):
    url = API_URL + method
    headers = {"Content-Type": "application/json"}
    payload = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        print(f"API Error ({method}): {e}")
        return None

def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id, 
        "text": text,
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return call_api("sendMessage", payload)

def edit_message_reply_markup(chat_id, message_id, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup}
    return call_api("editMessageReplyMarkup", payload)

def answer_callback_query(callback_query_id, text=None):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return call_api("answerCallbackQuery", payload)

# --- Core Logic ---

URL_PATTERN = re.compile(r'https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)')

def run_pipeline(url):
    """Executes the pipeline script in the background."""
    script_path = BASE_DIR / "pipeline" / "run_daily_pipeline.py"
    # Execute with python3 to ensure environment is correct
    cmd = [
        sys.executable, str(script_path),
        "--url", url,
        "--recipient-group", "all",
        "--enable-transcribe", "1",
        "--enable-summarize", "1",
        "--enable-mail", "1",
        "--enable-telegram", "1",
        "--telegram-progress"
    ]
    # We use Popen so the listener doesn't block while the pipeline runs
    subprocess.Popen(cmd, cwd=str(BASE_DIR))

def get_transcribe_status():
    """Checks the global lock to see if a transcription is in progress."""
    lock_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "gensrt.lock"
    if lock_dir.exists():
        pid_file = lock_dir / "pid"
        if pid_file.exists():
            pid = pid_file.read_text().strip()
            return f"🔴 忙碌中 (正在轉錄 PID: {pid})"
    return "🟢 空閒 (隨時可以執行)"

def format_url_for_display(url):
    """Formats a URL for display to avoid Telegram link previews."""
    try:
        parsed = urllib.parse.urlparse(url)
        if "youtube.com" in parsed.netloc or "youtu.be" in parsed.netloc:
            # Handle youtu.be/xxx -> youtube.com/watch?v=xxx
            if "youtu.be" in parsed.netloc:
                return f"[youtube] watch?v={parsed.path.lstrip('/')}"
            
            # Handle youtube.com/watch?v=xxx
            path_query = parsed.path.lstrip('/')
            if parsed.query:
                path_query += f"?{parsed.query}"
            return f"[youtube] {path_query}"
        elif "soundon.fm" in parsed.netloc:
            return f"[soundon] {parsed.path.lstrip('/')}"
    except Exception:
        pass
    return url

def handle_update(update):
    global OWNER_CHAT_ID
    
    # 1. Handle incoming messages (URL detection & Commands)
    if "message" in update:
        msg = update["message"]
        chat_id = str(msg.get("chat", {}).get("id"))
        text = msg.get("text", "")

        if OWNER_CHAT_ID and chat_id != OWNER_CHAT_ID:
            print(f"Ignored message from unauthorized chat: {chat_id}")
            return

        if text == "/status":
            status = get_transcribe_status()
            send_message(chat_id, f"目前系統狀態：\n{status}")
            return

        urls = URL_PATTERN.findall(text)
        for url in urls:
            if "youtube.com" in url or "youtu.be" in url or "soundon.fm" in url:
                status = get_transcribe_status()
                msg_text = f"偵測到網址：\n{url}\n\n目前狀態：{status}\n\n是否啟動流程？"
                if "忙碌中" in status:
                    msg_text += "\n(備註：新任務將會進入排隊隊伍，等待目前任務完成後自動開始)"
                
                reply_markup = {
                    "inline_keyboard": [[
                        {"text": "✅ 確認執行", "callback_data": f"exec|{url}"},
                        {"text": "❌ 取消", "callback_data": "cancel"}
                    ]]
                }
                send_message(chat_id, msg_text, reply_markup=reply_markup)

    # 2. Handle button clicks (Callback Queries)
    elif "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb["id"]
        chat_id = cb["message"]["chat"]["id"]
        message_id = cb["message"]["message_id"]
        data = cb.get("data", "")

        if data.startswith("exec|"):
            url = data.split("|", 1)[1]
            answer_callback_query(cb_id, "任務已啟動！")
            edit_message_reply_markup(chat_id, message_id, reply_markup=None)
            send_message(chat_id, f"🚀 已啟動任務：\n{url}\n完成後將自動發送通知。")
            run_pipeline(url)
        elif data == "cancel":
            answer_callback_query(cb_id, "已取消")
            edit_message_reply_markup(chat_id, message_id, reply_markup=None)
            send_message(chat_id, "任務已取消。")

def main():
    print("Telegram Listener started. Polling for updates...")
    last_update_id = 0
    
    while True:
        try:
            # Use long polling (30s timeout) to be efficient
            updates = call_api("getUpdates", {"offset": last_update_id + 1, "timeout": 30})
            
            if updates and updates.get("ok"):
                for update in updates.get("result", []):
                    handle_update(update)
                    last_update_id = update["update_id"]
            
            # Short sleep to prevent tight loop in case of API issues
            time.sleep(1)
        except KeyboardInterrupt:
            print("\nListener stopped by user.")
            break
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
