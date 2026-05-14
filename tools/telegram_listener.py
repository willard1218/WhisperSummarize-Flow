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

def run_pipeline(url=None, chat_id=None, local_file=None):
    """Executes the pipeline script in the background."""
    script_path = BASE_DIR / "pipeline" / "run_daily_pipeline.py"
    cmd = [
        sys.executable, str(script_path),
        "--recipient-group", "all",
        "--enable-transcribe", "1",
        "--enable-summarize", "1",
        "--enable-mail", "1",
        "--enable-telegram", "1",
        "--telegram-progress",
        "--telegram-chat-id", str(chat_id)
    ]
    if url:
        cmd += ["--url", url]
    elif local_file:
        cmd += ["--local-file", str(local_file)]
    
    # We use Popen so the listener doesn't block while the pipeline runs
    subprocess.Popen(cmd, cwd=str(BASE_DIR))

def download_telegram_file(file_id, dest_path):
    """Downloads a file from Telegram servers."""
    res = call_api("getFile", {"file_id": file_id})
    if res and res.get("ok"):
        file_path = res["result"]["file_path"]
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        print(f"Downloading {url} to {dest_path}")
        urllib.request.urlretrieve(url, dest_path)
        return True
    return False

def handle_update(update):
    global OWNER_CHAT_ID
    
    # 1. Handle incoming messages (URL detection, Commands, Voice)
    if "message" in update:
        msg = update["message"]
        chat_id = str(msg.get("chat", {}).get("id"))
        text = msg.get("text", "")
        print(f"Incoming message from chat_id: {chat_id}")

        if OWNER_CHAT_ID and chat_id != OWNER_CHAT_ID:
            print(f"Ignored message from unauthorized chat: {chat_id}")
            return

        if text == "/status":
            status = get_transcribe_status()
            send_message(chat_id, f"目前系統狀態：\n{status}")
            return

        # Handle Voice/Audio Messages
        audio_msg = None
        if "voice" in msg:
            audio_msg = msg["voice"]
            kind = "voice"
            ext = "ogg"
            orig_name = f"voice_{int(time.time())}.ogg"
        elif "audio" in msg:
            audio_msg = msg["audio"]
            kind = "audio"
            orig_name = audio_msg.get("file_name", f"audio_{int(time.time())}.mp3")
            ext = orig_name.split(".")[-1]
        elif "document" in msg:
            doc = msg["document"]
            mime = doc.get("mime_type", "")
            orig_name = doc.get("file_name", "")
            if mime.startswith("audio/") or orig_name.lower().endswith((".m4a", ".mp3", ".wav", ".ogg", ".flac", ".aac")):
                audio_msg = doc
                kind = "document"
                if not orig_name:
                    orig_name = f"doc_{int(time.time())}.m4a"
                ext = orig_name.split(".")[-1]

        if audio_msg:
            file_id = audio_msg["file_id"]
            duration = audio_msg.get("duration", 0)
            
            print(f"--- Audio Message Received ---")
            print(f"Chat ID: {chat_id}")
            print(f"Kind: {kind}")
            print(f"Original Name: {orig_name}")
            print(f"Duration: {duration}s")
            print(f"File ID: {file_id}")
            
            # Clean filename for safety but try to keep some meaning
            safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", orig_name)
            if not safe_name or safe_name.startswith("___"):
                safe_name = f"{kind}_{int(time.time())}.{ext}"
            
            voice_dir = BASE_DIR / "output" / "voice"
            voice_dir.mkdir(parents=True, exist_ok=True)
            dest_path = voice_dir / safe_name
            
            send_message(chat_id, f"🎙️ 收到音訊檔案\n類型：{kind}\n檔名：{orig_name}\n長度：{duration}秒\n\n正在下載並準備轉錄...")
            
            if download_telegram_file(file_id, dest_path):
                print(f"Successfully downloaded to: {dest_path}")
                run_pipeline(local_file=dest_path, chat_id=chat_id)
            else:
                print(f"Failed to download file_id: {file_id}")
                send_message(chat_id, "❌ 下載檔案失敗。")
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
            run_pipeline(url, chat_id)
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
