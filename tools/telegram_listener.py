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


logger = logging.getLogger("telegram_listener")



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
            logger.info(f"[API CALL] Method: {method}, Data: {data}")

        for attempt in range(3):
            request = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    res_data = json.loads(response.read().decode())
                    if not res_data.get("ok"):
                        logger.error(f"[API ERROR] {method}: {res_data.get('description')}")
                    return res_data
            except Exception as exc:
                logger.warning(f"[API ATTEMPT {attempt+1} FAILED] {method}: {exc}")
                if attempt < 2:
                    time.sleep(2)
                else:
                    return None
        return None

    def send_message(self, chat_id: str | int, text: str, reply_markup: dict | None = None) -> dict | None:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self.call_api("sendMessage", payload)

    def send_document(self, chat_id: str | int, file_path: Path, caption: str | None = None) -> dict | None:
        """Sends a document (file) to a Telegram chat using multipart/form-data."""
        url = self.settings.api_url + "sendDocument"
        boundary = "----------Boundary" + hashlib.md5(str(time.time()).encode()).hexdigest()
        
        parts = []
        # Add chat_id part
        parts.append(f"--{boundary}")
        parts.append('Content-Disposition: form-data; name="chat_id"')
        parts.append("")
        parts.append(str(chat_id))
        
        # Add caption part if exists
        if caption:
            parts.append(f"--{boundary}")
            parts.append('Content-Disposition: form-data; name="caption"')
            parts.append("")
            parts.append(caption)
            
        # Add file part
        parts.append(f"--{boundary}")
        parts.append(f'Content-Disposition: form-data; name="document"; filename="{file_path.name}"')
        parts.append("Content-Type: application/octet-stream")
        parts.append("")
        
        with open(file_path, "rb") as f:
            file_content = f.read()
            
        body = b"\r\n".join([p.encode("utf-8") if isinstance(p, str) else p for p in parts])
        body += b"\r\n" + file_content + b"\r\n--" + boundary.encode("utf-8") + b"--\r\n"
        
        req = urllib.request.Request(url, data=body)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        
        logger.info(f"[API CALL] sendDocument: {file_path}")
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            logger.error(f"[API ERROR] sendDocument failed: {e}")
            return None

    def edit_message_reply_markup(self, chat_id: str | int, message_id: int, reply_markup: dict | None = None) -> dict | None:
        payload = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.call_api("editMessageReplyMarkup", payload)

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> dict | None:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        return self.call_api("answerCallbackQuery", payload)

    def get_updates(self, offset: int, timeout: int = 30) -> dict | None:
        return self.call_api("getUpdates", {"offset": offset, "timeout": timeout})

    def get_file(self, file_id: str) -> dict | None:
        return self.call_api("getFile", {"file_id": file_id})

    def delete_webhook(self, drop_pending_updates: bool = False) -> dict | None:
        return self.call_api("deleteWebhook", {"drop_pending_updates": drop_pending_updates})


class UrlTaskStore:
    """Stores full URLs and provides a short ID for Telegram callback_data limits."""
    def __init__(self, store_path: Path):
        self.path = store_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

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
        if not path.exists():
            return False
        try:
            with open(path, "r") as f:
                # Try to acquire an exclusive lock, non-blocking
                # If this succeeds, it means no one else has the lock
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # We got the lock! So it's NOT busy.
                fcntl.flock(f, fcntl.LOCK_UN)
                return False
        except (IOError, BlockingIOError):
            # Lock is held by someone else
            return True
        except Exception:
            # Fallback to simple existence check if anything weird happens
            return path.exists()

    def _is_dir_lock_busy(self, path: Path) -> bool:
        if not path.exists():
            return False
        pid_file = path / "pid"
        if not pid_file.exists():
            # Directory exists but no pid file? Might be transitioning or stale.
            return True
        try:
            pid = int(pid_file.read_text().strip())
            # Check if process is alive
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, ValueError, PermissionError):
            # Process is dead or invalid pid
            return False

    def is_busy(self) -> bool:
        # Check whisper_transcription.lock (python flock)
        if self._is_flock_busy(self.lock_paths[0]):
            return True
        
        # Check gensrt.lock (shell directory lock)
        if self._is_dir_lock_busy(self.lock_paths[1]):
            return True
            
        return False

    def describe(self) -> str:
        return "忙碌中" if self.is_busy() else "空閒中"


class PipelineLauncher:
    def __init__(self, base_dir: Path, python_executable: str):
        self.base_dir = base_dir
        self.python_executable = python_executable
        self.script_path = self.base_dir / "pipeline" / "run_daily_pipeline.py"

    def build_command(self, chat_id: str | int, url: str | None = None, local_file: Path | None = None) -> list[str]:
        command = [
            self.python_executable,
            str(self.script_path),
            "--recipient-group", "all",
            "--enable-transcribe", "1",
            "--enable-summarize", "1",
            "--enable-mail", "1",
            "--enable-telegram", "1",
            "--telegram-progress",
            "--telegram-chat-id", str(chat_id),
            "--task-origin", "telegram",
        ]
        if url:
            command += ["--url", url]
        elif local_file:
            command += ["--local-file", str(local_file)]
        return command

    def run(self, chat_id: str | int, url: str | None = None, local_file: Path | None = None) -> None:
        command = self.build_command(chat_id=chat_id, url=url, local_file=local_file)
        subprocess.Popen(command, cwd=str(self.base_dir))


class TelegramFileDownloader:
    def __init__(self, api_client: TelegramApiClient, bot_token: str):
        self.api_client = api_client
        self.bot_token = bot_token

    def download(self, file_id: str, destination: Path) -> bool:
        response = self.api_client.get_file(file_id)
        if not response or not response.get("ok"):
            return False

        file_path = response["result"]["file_path"]
        url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
        logger.info(f"[DOWNLOADING] {url} to {destination}")
        urllib.request.urlretrieve(url, destination)
        return True


class MessageInterpreter:
    @staticmethod
    def combined_text(message: dict) -> str:
        text = message.get("text", "")
        caption = message.get("caption", "")
        return f"{text}\n{caption}".strip()

    @staticmethod
    def extract_supported_urls(text: str) -> list[str]:
        urls = URL_PATTERN.findall(text)
        return [url for url in urls if any(host in url for host in SUPPORTED_URL_HOSTS)]

    @staticmethod
    def extract_media(message: dict) -> MediaMessage | None:
        timestamp = int(time.time())

        if "voice" in message:
            return MediaMessage(
                file_id=message["voice"]["file_id"],
                kind="voice",
                original_name=f"voice_{timestamp}.ogg",
                extension="ogg",
                duration=message["voice"].get("duration", 0),
            )

        if "audio" in message:
            audio = message["audio"]
            original_name = audio.get("file_name", f"audio_{timestamp}.mp3")
            return MediaMessage(
                file_id=audio["file_id"],
                kind="audio",
                original_name=original_name,
                extension=original_name.rsplit(".", 1)[-1],
                duration=audio.get("duration", 0),
            )

        if "video" in message:
            video = message["video"]
            original_name = video.get("file_name", f"video_{timestamp}.mp4")
            return MediaMessage(
                file_id=video["file_id"],
                kind="video",
                original_name=original_name,
                extension=original_name.rsplit(".", 1)[-1] if "." in original_name else "mp4",
                duration=video.get("duration", 0),
            )

        if "video_note" in message:
            video_note = message["video_note"]
            return MediaMessage(
                file_id=video_note["file_id"],
                kind="video_note",
                original_name=f"video_note_{timestamp}.mp4",
                extension="mp4",
                duration=video_note.get("duration", 0),
            )

        if "document" in message:
            document = message["document"]
            mime = document.get("mime_type", "")
            original_name = document.get("file_name", "")
            if mime.startswith(("audio/", "video/")) or original_name.lower().endswith(MEDIA_EXTENSIONS):
                if not original_name:
                    original_name = f"document_{timestamp}.m4a"
                return MediaMessage(
                    file_id=document["file_id"],
                    kind="document",
                    original_name=original_name,
                    extension=original_name.rsplit(".", 1)[-1],
                    duration=document.get("duration", 0),
                )

        return None

    @staticmethod
    def safe_filename(original_name: str, fallback_kind: str, extension: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", original_name)
        if not cleaned or cleaned.startswith("___"):
            return f"{fallback_kind}_{int(time.time())}.{extension}"
        return cleaned


class TelegramUpdateHandler:
    def __init__(
        self,
        settings: ListenerSettings,
        api_client: TelegramApiClient,
        status_provider: TranscriptionStatusProvider,
        pipeline_launcher: PipelineLauncher,
        file_downloader: TelegramFileDownloader,
        url_task_store: UrlTaskStore,
        interpreter: MessageInterpreter | None = None,
    ):
        self.settings = settings
        self.api_client = api_client
        self.status_provider = status_provider
        self.pipeline_launcher = pipeline_launcher
        self.file_downloader = file_downloader
        self.url_task_store = url_task_store
        self.interpreter = interpreter or MessageInterpreter()

    def handle(self, update: dict) -> None:
        if "message" in update:
            self._handle_message(update["message"])
            return
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])

    def _handle_message(self, message: dict) -> None:
        chat_id = str(message.get("chat", {}).get("id"))
        text = message.get("text", "")
        message_id = message.get("message_id")
        logger.info(f"[RECV MESSAGE] Chat: {chat_id}, ID: {message_id}, Text: {text[:100]}...")

        if self.settings.owner_chat_id and chat_id != self.settings.owner_chat_id:
            logger.warning(f"[IGNORED] Unauthorized chat: {chat_id}")
            return

        if text == "/status":
            system_status = self.status_provider.describe()
            try:
                # Execute check_daily_status.py and capture its output
                cmd = [self.settings.python_executable, str(self.settings.base_dir / "tools" / "check_daily_status.py")]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                daily_status = res.stdout.strip() if res.returncode == 0 else "無法取得今日處理狀態。"
            except Exception as e:
                logger.error(f"[STATUS ERROR] Failed to run check_daily_status.py: {e}")
                daily_status = "讀取狀態時發生錯誤。"
            
            self.api_client.send_message(chat_id, f"目前系統狀態：\n{system_status}\n\n{daily_status}")
            return

        if text == "/dump_log":
            log_path = self.settings.base_dir / "logs" / "telegram_listener.log"
            if log_path.exists():
                self.api_client.send_document(chat_id, log_path, caption="目前系統日誌 (telegram_listener.log)")
            else:
                self.api_client.send_message(chat_id, "找不到日誌檔案。")
            return

        if text.lower().startswith("/ai_talk "):
            prompt = text[len("/ai_talk "):].strip()
            if not prompt:
                # This case is less likely with the space check, but keep for safety
                self.api_client.send_message(chat_id, "請提供指令。用法: /ai_talk {您的指令}")
                return
            
            self.api_client.send_message(chat_id, "正在處理 AI 請求 (YOLO 模式)，請稍候...")
            try:
                # Execute gemini cli in YOLO mode with --prompt for non-interactive execution
                # Remove 'cli' keyword to avoid positional argument conflict with --prompt
                cmd = ["/opt/homebrew/bin/gemini", "--yolo", "--prompt", prompt]
                logger.info(f"[AI_TALK] Executing: {cmd}")
                
                # Use a larger timeout for AI tasks
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(self.settings.base_dir))
                
                output = res.stdout.strip()
                if not output and res.stderr:
                    output = f"執行出錯：\n{res.stderr.strip()}"
                elif not output:
                    output = "AI 執行完成，但無輸出內容。"
                
                # Strip potential ANSI escape codes (simple regex)
                clean_output = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', output)
                
                self.api_client.send_message(chat_id, f"AI 回應：\n\n{clean_output}")
            except subprocess.TimeoutExpired:
                self.api_client.send_message(chat_id, "AI 執行超時 (5分鐘)。")
            except Exception as e:
                logger.error(f"[AI_TALK ERROR] {e}")
                self.api_client.send_message(chat_id, f"發生錯誤：{e}")
            return

        media = self.interpreter.extract_media(message)
        if media:
            logger.info(f"[MEDIA DETECTED] Chat: {chat_id}, Kind: {media.kind}, Name: {media.original_name}")
            self._handle_media(chat_id, media)
            return

        combined_text = self.interpreter.combined_text(message)
        urls = self.interpreter.extract_supported_urls(combined_text)
        if urls:
            logger.info(f"[URLS EXTRACTED] Chat: {chat_id}, Count: {len(urls)}, URLs: {urls}")
        for url in urls:
            self._send_url_confirmation(chat_id, url)

    def _handle_media(self, chat_id: str, media: MediaMessage) -> None:
        logger.info(f"[PROCESSING MEDIA] Chat: {chat_id}, Kind: {media.kind}, File ID: {media.file_id}")

        safe_name = self.interpreter.safe_filename(media.original_name, media.kind, media.extension)
        task_dir = telegram_media_output_dir(self.settings.base_dir / "output", media.kind, media.original_name)
        destination = task_dir / safe_name
        write_task_metadata(
            task_dir,
            {
                "task_origin": "telegram",
                "kind": media.kind,
                "created_at": datetime.now().astimezone().isoformat(),
                "chat_id": chat_id,
                "original_name": media.original_name,
                "file_id": media.file_id,
                "duration": media.duration,
            },
        )

        self.api_client.send_message(
            chat_id,
            f"收到媒體檔案\n類型：{media.kind}\n檔名：{media.original_name}\n長度：{media.duration}秒\n\n正在下載並準備轉錄...",
        )

        if self.file_downloader.download(media.file_id, destination):
            logger.info(f"[DOWNLOAD SUCCESS] Path: {destination}")
            self.pipeline_launcher.run(chat_id=chat_id, local_file=destination)
            return

        logger.error(f"[DOWNLOAD FAILED] File ID: {media.file_id}")
        self.api_client.send_message(chat_id, "下載檔案失敗。")

    def _send_url_confirmation(self, chat_id: str, url: str) -> None:
        status = self.status_provider.describe()
        message = f"偵測到網址：\n{url}\n\n目前狀態：{status}\n\n是否啟動流程？"
        if status == "忙碌中":
            message += "\n(備註：新任務將會進入排隊隊伍，等待目前任務完成後自動開始)"

        url_id = self.url_task_store.get_id_for_url(url)
        reply_markup = {
            "inline_keyboard": [[
                {"text": "確認執行", "callback_data": f"exec|{url_id}"},
                {"text": "取消", "callback_data": "cancel"},
            ]]
        }
        logger.info(f"[SEND CONFIRMATION] Chat: {chat_id}, URL: {url}")
        self.api_client.send_message(chat_id, message, reply_markup=reply_markup)

    def _handle_callback(self, callback_query: dict) -> None:
        callback_id = callback_query["id"]
        chat_id = callback_query["message"]["chat"]["id"]
        message_id = callback_query["message"]["message_id"]
        data = callback_query.get("data", "")
        logger.info(f"[RECV CALLBACK] Chat: {chat_id}, Message ID: {message_id}, Data: {data}")

        if data.startswith("exec|"):
            url_id = data.split("|", 1)[1]
            url = self.url_task_store.get_url_by_id(url_id)
            if not url:
                logger.error(f"[CALLBACK ERROR] URL not found for ID: {url_id}")
                self.api_client.answer_callback_query(callback_id, "錯誤：找不到原始網址")
                self.api_client.send_message(chat_id, "任務啟動失敗：找不到原始網址。請重新傳送連結。")
                return

            logger.info(f"[TASK STARTING] Chat: {chat_id}, URL: {url}")
            self.api_client.answer_callback_query(callback_id, "任務已啟動")
            self.api_client.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
            self.api_client.send_message(chat_id, f"已啟動任務：\n{url}\n完成後將自動發送通知。")
            self.pipeline_launcher.run(chat_id=chat_id, url=url)
            return

        if data == "cancel":
            logger.info(f"[TASK CANCELLED] Chat: {chat_id}")
            self.api_client.answer_callback_query(callback_id, "已取消")
            self.api_client.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
            self.api_client.send_message(chat_id, "任務已取消。")


class TelegramPoller:
    def __init__(self, api_client: TelegramApiClient, update_handler: TelegramUpdateHandler, sleep_seconds: int = 1):
        self.api_client = api_client
        self.update_handler = update_handler
        self.sleep_seconds = sleep_seconds
        self.last_update_id = 0

    def poll_once(self) -> None:
        updates = self.api_client.get_updates(self.last_update_id + 1, timeout=30)
        if not updates or not updates.get("ok"):
            return

        for update in updates.get("result", []):
            try:
                logger.info(f"[RECV UPDATE] Update ID: {update.get('update_id')}")
                self.update_handler.handle(update)
            except Exception as exc:
                logger.error(f"[UPDATE ERROR] {update.get('update_id')}: {exc}")
            finally:
                self.last_update_id = update["update_id"]

    def run_forever(self) -> None:
        logger.info("Telegram Listener started. Polling for updates...")
        while True:
            try:
                self.poll_once()
                time.sleep(self.sleep_seconds)
            except KeyboardInterrupt:
                logger.info("Listener stopped by user.")
                break
            except Exception as exc:
                logger.error(f"[LOOP ERROR] {exc}")
                time.sleep(10)


def build_settings(base_dir: Path = BASE_DIR) -> ListenerSettings:
    load_local_config(base_dir / "config" / "local_config.sh", os.environ)
    return ListenerSettings(
        base_dir=base_dir,
        bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        owner_chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
        python_executable=sys.executable,
    )


def build_poller(settings: ListenerSettings) -> TelegramPoller:
    api_client = TelegramApiClient(settings)
    status_provider = TranscriptionStatusProvider()
    pipeline_launcher = PipelineLauncher(settings.base_dir, settings.python_executable)
    file_downloader = TelegramFileDownloader(api_client, settings.bot_token)
    url_task_store = UrlTaskStore(settings.base_dir / "output" / "telegram" / "url_tasks.json")
    update_handler = TelegramUpdateHandler(
        settings=settings,
        api_client=api_client,
        status_provider=status_provider,
        pipeline_launcher=pipeline_launcher,
        file_downloader=file_downloader,
        url_task_store=url_task_store,
    )
    return TelegramPoller(api_client, update_handler)


def setup_logging(base_dir: Path) -> None:
    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "telegram_listener.log"
    
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    logger.setLevel(logging.INFO)


class SingletonLock:
    def __init__(self, lock_file: Path):
        self.lock_file = lock_file
        self.fd = None

    def acquire(self) -> bool:
        try:
            self.fd = open(self.lock_file, "w")
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fd.write(str(os.getpid()))
            self.fd.flush()
            return True
        except (IOError, BlockingIOError):
            return False

    def release(self) -> None:
        if self.fd:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                self.fd.close()
            except Exception:
                pass
            if self.lock_file.exists():
                try:
                    self.lock_file.unlink()
                except Exception:
                    pass


def main() -> int:
    settings = build_settings()
    setup_logging(settings.base_dir)

    lock = SingletonLock(Path("/tmp/telegram_listener.pid"))
    if not lock.acquire():
        # Do not use logger here if logging is not fully initialized or might conflict, 
        # but setup_logging was already called.
        logger.error("Another instance of telegram_listener.py is already running. Exiting.")
        return 1

    try:
        if not settings.bot_token:
            logger.error("TELEGRAM_BOT_TOKEN not found in local_config.sh")
            return 1

        api_client = TelegramApiClient(settings)
        logger.info("Clearing any existing webhooks...")
        api_client.delete_webhook()

        build_poller(settings).run_forever()
    finally:
        lock.release()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
