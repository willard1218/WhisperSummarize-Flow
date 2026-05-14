#!/usr/bin/env python3

from __future__ import annotations

import json
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


URL_PATTERN = re.compile(
    r"https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b"
    r"(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)"
)
SUPPORTED_URL_HOSTS = ("youtube.com", "youtu.be", "soundon.fm")
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
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode())
        except Exception as exc:
            print(f"API Error ({method}): {exc}")
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

    def edit_message_reply_markup(self, chat_id: str | int, message_id: int, reply_markup: dict | None = None) -> dict | None:
        return self.call_api(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
        )

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> dict | None:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        return self.call_api("answerCallbackQuery", payload)

    def get_updates(self, offset: int, timeout: int = 30) -> dict | None:
        return self.call_api("getUpdates", {"offset": offset, "timeout": timeout})

    def get_file(self, file_id: str) -> dict | None:
        return self.call_api("getFile", {"file_id": file_id})


class TranscriptionStatusProvider:
    def __init__(self, lock_paths: list[Path] | None = None):
        self.lock_paths = lock_paths or [
            Path("/tmp/whisper_transcription.lock"),
            Path(os.environ.get("TMPDIR", "/tmp")) / "gensrt.lock",
        ]

    def is_busy(self) -> bool:
        return any(path.exists() for path in self.lock_paths)

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
        print(f"Downloading {url} to {destination}")
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
                extension=original_name.rsplit(".", 1)[-1],
                duration=video.get("duration", 0),
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
        interpreter: MessageInterpreter | None = None,
    ):
        self.settings = settings
        self.api_client = api_client
        self.status_provider = status_provider
        self.pipeline_launcher = pipeline_launcher
        self.file_downloader = file_downloader
        self.interpreter = interpreter or MessageInterpreter()

    def handle(self, update: dict) -> None:
        if "message" in update:
            self._handle_message(update["message"])
            return
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])

    def _handle_message(self, message: dict) -> None:
        chat_id = str(message.get("chat", {}).get("id"))
        print(f"Incoming message from chat_id: {chat_id}")

        if self.settings.owner_chat_id and chat_id != self.settings.owner_chat_id:
            print(f"Ignored message from unauthorized chat: {chat_id}")
            return

        text = message.get("text", "")
        if text == "/status":
            self.api_client.send_message(chat_id, f"目前系統狀態：\n{self.status_provider.describe()}")
            return

        media = self.interpreter.extract_media(message)
        if media:
            self._handle_media(chat_id, media)
            return

        combined_text = self.interpreter.combined_text(message)
        for url in self.interpreter.extract_supported_urls(combined_text):
            self._send_url_confirmation(chat_id, url)

    def _handle_media(self, chat_id: str, media: MediaMessage) -> None:
        print("--- Audio/Video Message Received ---")
        print(f"Chat ID: {chat_id}")
        print(f"Kind: {media.kind}")
        print(f"Original Name: {media.original_name}")
        print(f"Duration: {media.duration}s")
        print(f"File ID: {media.file_id}")

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
            print(f"Successfully downloaded to: {destination}")
            self.pipeline_launcher.run(chat_id=chat_id, local_file=destination)
            return

        print(f"Failed to download file_id: {media.file_id}")
        self.api_client.send_message(chat_id, "下載檔案失敗。")

    def _send_url_confirmation(self, chat_id: str, url: str) -> None:
        status = self.status_provider.describe()
        message = f"偵測到網址：\n{url}\n\n目前狀態：{status}\n\n是否啟動流程？"
        if status == "忙碌中":
            message += "\n(備註：新任務將會進入排隊隊伍，等待目前任務完成後自動開始)"

        reply_markup = {
            "inline_keyboard": [[
                {"text": "確認執行", "callback_data": f"exec|{url}"},
                {"text": "取消", "callback_data": "cancel"},
            ]]
        }
        self.api_client.send_message(chat_id, message, reply_markup=reply_markup)

    def _handle_callback(self, callback_query: dict) -> None:
        callback_id = callback_query["id"]
        chat_id = callback_query["message"]["chat"]["id"]
        message_id = callback_query["message"]["message_id"]
        data = callback_query.get("data", "")

        if data.startswith("exec|"):
            url = data.split("|", 1)[1]
            self.api_client.answer_callback_query(callback_id, "任務已啟動")
            self.api_client.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
            self.api_client.send_message(chat_id, f"已啟動任務：\n{url}\n完成後將自動發送通知。")
            self.pipeline_launcher.run(chat_id=chat_id, url=url)
            return

        if data == "cancel":
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
                self.update_handler.handle(update)
            except Exception as exc:
                print(f"Update Error ({update.get('update_id')}): {exc}")
            finally:
                self.last_update_id = update["update_id"]

    def run_forever(self) -> None:
        print("Telegram Listener started. Polling for updates...")
        while True:
            try:
                self.poll_once()
                time.sleep(self.sleep_seconds)
            except KeyboardInterrupt:
                print("\nListener stopped by user.")
                break
            except Exception as exc:
                print(f"Loop Error: {exc}")
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
    update_handler = TelegramUpdateHandler(
        settings=settings,
        api_client=api_client,
        status_provider=status_provider,
        pipeline_launcher=pipeline_launcher,
        file_downloader=file_downloader,
    )
    return TelegramPoller(api_client, update_handler)


def main() -> int:
    settings = build_settings()
    if not settings.bot_token:
        print("Error: TELEGRAM_BOT_TOKEN not found in local_config.sh")
        return 1

    build_poller(settings).run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
