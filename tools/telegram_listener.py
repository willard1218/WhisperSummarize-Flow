#!/usr/bin/env python3

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "tools"))

from local_config import load_local_config
from output_paths import telegram_media_output_dir, write_task_metadata
from logger import setup_logging, get_logger, TaskLogger

logger = get_logger("telegram_listener")

URL_PATTERN = re.compile(
    r"https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b"
    r"(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)"
)
SUPPORTED_URL_HOSTS = ("youtube.com", "youtu.be", "soundon.fm", "podcasts.apple.com")
MEDIA_EXTENSIONS = (".m4a", ".mp3", ".wav", ".ogg", ".flac", ".aac", ".mp4", ".mov", ".mkv")

@dataclass(frozen=True)
class ListenerSettings:
    base_dir: Path
    bot_token: str
    owner_chat_id: str | None
    python_executable: str

    @property
    def api_url(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/"

@dataclass(frozen=True)
class MediaMessage:
    file_id: str
    kind: str
    original_name: str
    extension: str
    duration: int

class TelegramApiClient:
    def __init__(self, settings: ListenerSettings):
        self.settings = settings

    def call_api(self, method: str, data: dict | None = None) -> dict | None:
        url = self.settings.api_url + method
        payload = json.dumps(data).encode("utf-8") if data else None
        
        if method != "getUpdates":
            logger.info(f"API request method={method}", action="api_call")

        for attempt in range(3):
            request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    res_data = json.loads(response.read().decode())
                    if not res_data.get("ok"):
                        logger.error(f"API error method={method} desc=\"{res_data.get('description')}\"", action="api_error")
                    return res_data
            except Exception as exc:
                logger.warning(f"API retry attempt={attempt+1} method={method} error=\"{exc}\"", action="api_retry")
                if attempt < 2:
                    time.sleep(2)
                else:
                    return None
        return None

    def send_message(self, chat_id: str | int, text: str, reply_markup: dict | None = None) -> dict | None:
        """Sends a text message, automatically chunking if it exceeds the Telegram character limit."""
        max_len = 4000
        chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)]
        
        last_res = None
        logger.info(f"Outgoing Telegram message chat_id={chat_id} chunks={len(chunks)} body=\"{text[:100]}...\"", action="send_message")
        
        for i, chunk in enumerate(chunks):
            if i > 0:
                time.sleep(0.5) # Avoid hitting rate limits for multi-chunk messages
            payload = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
            if reply_markup and i == len(chunks) - 1:
                # Only attach reply_markup to the last chunk
                payload["reply_markup"] = reply_markup
            
            last_res = self.call_api("sendMessage", payload)
            if not last_res or not last_res.get("ok"):
                logger.error(f"Failed to send message chunk {i+1}/{len(chunks)}", action="chunk_error")
                
        return last_res

    def send_document(self, chat_id: str | int, file_path: Path, caption: str | None = None) -> dict | None:
        url = self.settings.api_url + "sendDocument"
        boundary = "----------Boundary" + hashlib.md5(str(time.time()).encode()).hexdigest()
        parts = []
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}")
        if caption:
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}")
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{file_path.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n")
        
        with open(file_path, "rb") as f: file_content = f.read()
        body = b"\r\n".join([p.encode("utf-8") if isinstance(p, str) else p for p in parts])
        body += file_content + b"\r\n--" + boundary.encode("utf-8") + b"--\r\n"
        
        req = urllib.request.Request(url, data=body)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        
        logger.info(f"Outgoing Telegram document chat_id={chat_id} path={file_path} caption=\"{caption or ''}\"", action="api_document")
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            logger.error(f"API document failed error=\"{e}\"", action="api_error")
            return None

    def edit_message_reply_markup(self, chat_id: str | int, message_id: int, reply_markup: dict | None = None) -> dict | None:
        payload = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup is not None: payload["reply_markup"] = reply_markup
        return self.call_api("editMessageReplyMarkup", payload)

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> dict | None:
        payload = {"callback_query_id": callback_query_id}
        if text: payload["text"] = text
        return self.call_api("answerCallbackQuery", payload)

    def get_updates(self, offset: int, timeout: int = 30) -> dict | None:
        return self.call_api("getUpdates", {"offset": offset, "timeout": timeout})

    def get_file(self, file_id: str) -> dict | None:
        return self.call_api("getFile", {"file_id": file_id})

    def delete_webhook(self, drop_pending_updates: bool = False) -> dict | None:
        return self.call_api("deleteWebhook", {"drop_pending_updates": drop_pending_updates})

class UrlTaskStore:
    def __init__(self, store_path: Path):
        self.path = store_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if not self.path.exists(): return {}
        try: return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception: return {}

    def _save(self, data: dict):
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_id_for_url(self, url: str) -> str:
        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:16]
        data = self._load()
        data[url_hash] = url
        self._save(data)
        return url_hash

    def get_url_by_id(self, url_id: str) -> str | None:
        return self._load().get(url_id)

class TranscriptionStatusProvider:
    def __init__(self, lock_paths: list[Path] | None = None):
        self.lock_paths = lock_paths or [
            Path("/tmp/whisper_transcription.lock"),
            Path(os.environ.get("TMPDIR", "/tmp")) / "gensrt.lock",
        ]

    def _is_flock_busy(self, path: Path) -> bool:
        if not path.exists(): return False
        try:
            with open(path, "r") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                return False
        except (IOError, BlockingIOError): return True
        except Exception: return path.exists()

    def _is_dir_lock_busy(self, path: Path) -> bool:
        if not path.exists(): return False
        pid_file = path / "pid"
        if not pid_file.exists(): return True
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, ValueError, PermissionError): return False

    def is_busy(self) -> bool:
        return self._is_flock_busy(self.lock_paths[0]) or self._is_dir_lock_busy(self.lock_paths[1])

    def describe(self) -> str:
        return "忙碌中" if self.is_busy() else "空閒中"

class PipelineLauncher:
    def __init__(self, base_dir: Path, python_executable: str):
        self.base_dir = base_dir
        self.python_executable = python_executable
        self.script_path = self.base_dir / "pipeline" / "run_daily_pipeline.py"

    def build_command(self, chat_id: str | int, url: str | None = None, local_file: Path | None = None) -> list[str]:
        command = [
            self.python_executable, str(self.script_path),
            "--recipient-group", "all",
            "--enable-transcribe", "1",
            "--enable-summarize", "1",
            "--enable-mail", "1",
            "--enable-telegram", "1",
            "--telegram-progress",
            "--telegram-chat-id", str(chat_id),
            "--task-origin", "telegram",
            "--log-file", str(self.base_dir / "logs" / "telegram_listener.log"),
        ]
        if url: command += ["--url", url]
        elif local_file: command += ["--local-file", str(local_file)]
        return command

    def run(self, chat_id: str | int, url: str | None = None, local_file: Path | None = None) -> None:
        command = self.build_command(chat_id=chat_id, url=url, local_file=local_file)
        # Optimized for AI debugging: Log the exact reproduction command
        logger.info(f"Launching pipeline command=\"{' '.join(command)}\"", action="launcher")
        subprocess.Popen(command, cwd=str(self.base_dir))

class TelegramFileDownloader:
    def __init__(self, api_client: TelegramApiClient, bot_token: str):
        self.api_client = api_client
        self.bot_token = bot_token

    def download(self, file_id: str, destination: Path) -> bool:
        response = self.api_client.get_file(file_id)
        if not response or not response.get("ok"): return False
        file_path = response["result"]["file_path"]
        url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
        logger.info(f"Downloading Telegram file url={url} dest={destination}", action="downloader")
        urllib.request.urlretrieve(url, destination)
        return True

class MessageInterpreter:
    @staticmethod
    def combined_text(message: dict) -> str:
        return f"{message.get('text', '')}\n{message.get('caption', '')}".strip()
    @staticmethod
    def extract_supported_urls(text: str) -> list[str]:
        urls = URL_PATTERN.findall(text)
        return [url for url in urls if any(host in url for host in SUPPORTED_URL_HOSTS)]
    @staticmethod
    def extract_media(message: dict) -> MediaMessage | None:
        t = int(time.time())
        if "voice" in message:
            v = message["voice"]
            return MediaMessage(v["file_id"], "voice", f"voice_{t}.ogg", "ogg", v.get("duration", 0))
        if "audio" in message:
            a = message["audio"]
            name = a.get("file_name", f"audio_{t}.mp3")
            return MediaMessage(a["file_id"], "audio", name, name.rsplit(".", 1)[-1], a.get("duration", 0))
        if "video" in message:
            v = message["video"]
            name = v.get("file_name", f"video_{t}.mp4")
            return MediaMessage(v["file_id"], "video", name, name.rsplit(".", 1)[-1] if "." in name else "mp4", v.get("duration", 0))
        if "video_note" in message:
            v = message["video_note"]
            return MediaMessage(v["file_id"], "video_note", f"vn_{t}.mp4", "mp4", v.get("duration", 0))
        if "document" in message:
            d = message["document"]
            name = d.get("file_name", "")
            if d.get("mime_type", "").startswith(("audio/", "video/")) or name.lower().endswith(MEDIA_EXTENSIONS):
                if not name: name = f"doc_{t}.m4a"
                return MediaMessage(d["file_id"], "document", name, name.rsplit(".", 1)[-1], d.get("duration", 0))
        return None
    @staticmethod
    def safe_filename(original_name: str, fallback_kind: str, extension: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", original_name)
        return cleaned if cleaned and not cleaned.startswith("___") else f"{fallback_kind}_{int(time.time())}.{extension}"

class TelegramUpdateHandler:
    def __init__(self, settings: ListenerSettings, api_client: TelegramApiClient, status_provider: TranscriptionStatusProvider, pipeline_launcher: PipelineLauncher, file_downloader: TelegramFileDownloader, url_task_store: UrlTaskStore, interpreter: MessageInterpreter | None = None):
        self.settings = settings
        self.api_client = api_client
        self.status_provider = status_provider
        self.pipeline_launcher = pipeline_launcher
        self.file_downloader = file_downloader
        self.url_task_store = url_task_store
        self.interpreter = interpreter or MessageInterpreter()

    def handle(self, update: dict) -> None:
        if "message" in update: self._handle_message(update["message"])
        elif "callback_query" in update: self._handle_callback(update["callback_query"])

    def _handle_message(self, message: dict) -> None:
        chat_id = str(message.get("chat", {}).get("id"))
        text = message.get("text", "")
        msg_id = message.get("message_id")
        
        logger.info(f"Received message chat_id={chat_id} msg_id={msg_id} text=\"{text[:50]}\"", action="message_recv")

        if self.settings.owner_chat_id and chat_id != self.settings.owner_chat_id:
            logger.warning(f"Unauthorized access chat_id={chat_id}", action="auth_denied")
            return

        if text == "/status":
            sys_status = self.status_provider.describe()
            try:
                cmd = [self.settings.python_executable, str(self.settings.base_dir / "tools" / "check_daily_status.py")]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                daily_status = res.stdout.strip() if res.returncode == 0 else "Error getting daily status."
            except Exception: daily_status = "Error executing status script."
            self.api_client.send_message(chat_id, f"目前系統狀態：\n{sys_status}\n\n{daily_status}")
            return

        if text == "/dump_log":
            log_path = self.settings.base_dir / "logs" / "telegram_listener.log"
            if log_path.exists(): self.api_client.send_document(chat_id, log_path, caption="System Log")
            else: self.api_client.send_message(chat_id, "Log file not found.")
            return

        if text.lower().startswith("/ai_talk"):
            prompt = text[len("/ai_talk"):].strip()
            if not prompt:
                self.api_client.send_message(chat_id, "請提供指令。", reply_markup={"force_reply": True, "selective": True})
                return
            self.api_client.send_message(chat_id, "正在處理 AI 請求 (對話模式)...")
            try:
                # Use --resume latest to maintain conversation context
                cmd = ["/opt/homebrew/bin/gemini", "--yolo", "--skip-trust", "--resume", "latest", "--prompt", prompt]
                logger.info(f"Executing AI talk session=latest command=\"{' '.join(cmd)}\"", action="ai_talk")
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(self.settings.base_dir))
                output = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', res.stdout.strip() or res.stderr.strip() or "No output")
                self.api_client.send_message(chat_id, f"AI 回應：\n\n{output}")
            except Exception as e:
                logger.error(f"AI talk failed error=\"{e}\"", action="ai_talk_error")
                self.api_client.send_message(chat_id, f"發生錯誤：{e}")
            return

        if text == "/ai_reset":
            self.api_client.send_message(chat_id, "正在重置 AI 對話記憶...")
            try:
                # Starting a new session by NOT using resume
                cmd = ["/opt/homebrew/bin/gemini", "--yolo", "--skip-trust", "--prompt", "Hello! This is a fresh session. Please acknowledge and wait for my instructions."]
                subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=str(self.settings.base_dir))
                self.api_client.send_message(chat_id, "✅ 對話記憶已重置，現在可以開始新的討論。")
            except Exception as e:
                self.api_client.send_message(chat_id, f"重置失敗：{e}")
            return

        media = self.interpreter.extract_media(message)
        if media:
            logger.info(f"Media detected kind={media.kind} name=\"{media.original_name}\"", action="media_detected")
            self._handle_media(chat_id, media)
            return

        urls = self.interpreter.extract_supported_urls(self.interpreter.combined_text(message))
        if urls: logger.info(f"URLs extracted count={len(urls)} urls={urls}", action="url_detected")
        for url in urls: self._send_url_confirmation(chat_id, url)

    def _handle_media(self, chat_id: str, media: MediaMessage) -> None:
        safe_name = self.interpreter.safe_filename(media.original_name, media.kind, media.extension)
        task_dir = telegram_media_output_dir(self.settings.base_dir / "output", media.kind, media.original_name)
        dest = task_dir / safe_name
        write_task_metadata(task_dir, {"task_origin": "telegram", "kind": media.kind, "created_at": datetime.now().astimezone().isoformat(), "chat_id": chat_id, "original_name": media.original_name, "file_id": media.file_id, "duration": media.duration})
        
        self.api_client.send_message(chat_id, f"收到媒體：{media.original_name}\n正在下載...")
        if self.file_downloader.download(media.file_id, dest):
            self.pipeline_launcher.run(chat_id=chat_id, local_file=dest)
        else:
            self.api_client.send_message(chat_id, "下載失敗。")

    def _send_url_confirmation(self, chat_id: str, url: str) -> None:
        status = self.status_provider.describe()
        msg = f"偵測到網址：\n{url}\n\n目前狀態：{status}\n是否啟動？"
        url_id = self.url_task_store.get_id_for_url(url)
        markup = {"inline_keyboard": [[{"text": "確認執行", "callback_data": f"exec|{url_id}"}, {"text": "取消", "callback_data": "cancel"}]]}
        self.api_client.send_message(chat_id, msg, reply_markup=markup)

    def _handle_callback(self, cb: dict) -> None:
        cid, chat_id, msg_id, data = cb["id"], cb["message"]["chat"]["id"], cb["message"]["message_id"], cb.get("data", "")
        logger.info(f"Callback received chat_id={chat_id} data=\"{data}\"", action="callback_recv")
        if data.startswith("exec|"):
            url = self.url_task_store.get_url_by_id(data.split("|", 1)[1])
            if not url:
                self.api_client.answer_callback_query(cid, "錯誤：找不到網址")
                return
            self.api_client.answer_callback_query(cid, "任務已啟動")
            self.api_client.edit_message_reply_markup(chat_id, msg_id, reply_markup=None)
            self.api_client.send_message(chat_id, f"已啟動任務：\n{url}")
            self.pipeline_launcher.run(chat_id=chat_id, url=url)
        elif data == "cancel":
            self.api_client.answer_callback_query(cid, "已取消")
            self.api_client.edit_message_reply_markup(chat_id, msg_id, reply_markup=None)

class TelegramPoller:
    def __init__(self, api_client: TelegramApiClient, update_handler: TelegramUpdateHandler):
        self.api_client, self.update_handler, self.last_update_id = api_client, update_handler, 0
    def poll_once(self) -> None:
        updates = self.api_client.get_updates(self.last_update_id + 1)
        if updates and updates.get("ok"):
            for u in updates["result"]:
                try: self.update_handler.handle(u)
                except Exception as e: logger.error(f"Update error id={u.get('update_id')} error=\"{e}\"", action="poll_error")
                finally: self.last_update_id = u["update_id"]
    def run_forever(self) -> None:
        logger.info("Telegram Poller started", action="poller_start")
        while True:
            try: self.poll_once(); time.sleep(1)
            except KeyboardInterrupt: break
            except Exception as e: logger.error(f"Loop error error=\"{e}\"", action="poller_error"); time.sleep(10)

def build_settings() -> ListenerSettings:
    load_local_config(BASE_DIR / "config" / "local_config.sh", os.environ)
    return ListenerSettings(BASE_DIR, os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID"), sys.executable)

def main() -> int:
    settings = build_settings()
    setup_logging(format_type="kv", log_file=settings.base_dir / "logs" / "telegram_listener.log")
    
    # Singleton check
    lock_file = Path("/tmp/telegram_listener.pid")
    try:
        f = open(lock_file, "w")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(str(os.getpid()))
        f.flush()
    except (IOError, BlockingIOError):
        logger.error("Another instance is running. Exiting.", action="startup_fail")
        return 1

    if not settings.bot_token:
        logger.error("No Bot Token. Exiting.", action="startup_fail")
        return 1

    api_client = TelegramApiClient(settings)
    api_client.delete_webhook()
    
    poller = TelegramPoller(api_client, TelegramUpdateHandler(settings, api_client, TranscriptionStatusProvider(), PipelineLauncher(settings.base_dir, settings.python_executable), TelegramFileDownloader(api_client, settings.bot_token), UrlTaskStore(settings.base_dir / "output" / "telegram" / "url_tasks.json")))
    poller.run_forever()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
