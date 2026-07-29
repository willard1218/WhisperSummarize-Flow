#!/usr/bin/env python3

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from project_runtime import bootstrap_project, load_project_env

BASE_DIR = bootstrap_project(ROOT_DIR)

from tools.output_paths import telegram_media_output_dir, telegram_url_output_dir, write_task_metadata
from tools.logger import setup_logging, get_logger, TaskLogger
from tools.registry import Registry

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
        self.error_counts: dict[str, dict[str, int]] = {}
        self.last_error_report: float = 0

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
                error_type = type(exc).__name__
                if not (method == "getUpdates" and error_type == "URLError"):
                    self.error_counts.setdefault(method, {}).setdefault(error_type, 0)
                    self.error_counts[method][error_type] += 1
                logger.debug(f"API retry attempt={attempt+1} method={method} error=\"{exc}\"", action="api_retry")
                if attempt < 2:
                    time.sleep(2)
                else:
                    return None
        return None

    def _build_error_summary(self) -> str | None:
        if not self.error_counts:
            return None
        total = sum(c for m in self.error_counts.values() for c in m.values())
        lines = [f"📊 Telegram API 錯誤統計（過去 1 小時）", f"總計：{total} 次錯誤"]
        for method, errors in sorted(self.error_counts.items()):
            for etype, count in sorted(errors.items()):
                lines.append(f"  {method} — {etype}: {count} 次")
        return "\n".join(lines)

    def send_error_report_if_needed(self, owner_chat_id: str | int) -> None:
        now = time.time()
        if now - self.last_error_report < 3600:
            return
        summary = self._build_error_summary()
        if summary:
            self.send_message(owner_chat_id, summary)
        self.error_counts.clear()
        self.last_error_report = now

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

class TranscriptionStatusProvider:
    def __init__(self, lock_paths: list[Path] | None = None):
        self.lock_paths = lock_paths or [
            Path("/tmp/whisper_transcription.lock"),
            Path(os.environ.get("TMPDIR", "/tmp")) / "gensrt.lock",
        ]

    def _is_process_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            # Extra check: is it actually a relevant process?
            # We can check /proc/{pid}/cmdline on Linux or use ps on macOS
            res = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
            cmd = res.stdout.lower()
            return any(x in cmd for x in ["python", "whisper", "gensrt", "ffmpeg", "yt-dlp"])
        except (ProcessLookupError, PermissionError):
            return False

    def _check_lock_self_healing(self, path: Path) -> bool:
        if not path.exists():
            return False

        # 0. Handle directory-based locks (dir containing a "pid" file)
        if path.is_dir():
            pid_file = path / "pid"
            if pid_file.exists():
                content = pid_file.read_text().strip()
                if content.isdigit():
                    pid = int(content)
                    if self._is_process_alive(pid):
                        return True
                    else:
                        logger.warning(f"Stale dir lock detected for pid={pid} at {path}. Self-healing...", action="lock_cleanup")
                        try: import shutil; shutil.rmtree(path)
                        except: pass
                        return False
            return False

        # 1. Try to read PID from lock file
        try:
            content = path.read_text().strip()
            if content.isdigit():
                pid = int(content)
                if self._is_process_alive(pid):
                    return True
                else:
                    logger.warning(f"Stale lock detected for pid={pid} at {path}. Self-healing...", action="lock_cleanup")
                    try: path.unlink()
                    except: pass
                    return False
        except Exception:
            pass

        # 2. Fallback to flock check
        try:
            with open(path, "r") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                return False
        except (IOError, BlockingIOError):
            return True
        except Exception:
            return path.exists()

    def is_busy(self) -> bool:
        return self._check_lock_self_healing(self.lock_paths[0]) or self._check_lock_self_healing(self.lock_paths[1])

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

    def run(self, chat_id: str | int, url: str | None = None, local_file: Path | None = None, wait: bool = False) -> int:
        command = self.build_command(chat_id=chat_id, url=url, local_file=local_file)
        logger.info(f"Launching pipeline command=\"{' '.join(command)}\"", action="launcher")
        kwargs = dict(cwd=str(self.base_dir), start_new_session=True)
        if wait:
            res = subprocess.run(command, **kwargs)
            return res.returncode
        else:
            subprocess.Popen(command, **kwargs)
            return 0

class TaskWorker:
    def __init__(self, settings: ListenerSettings, registry: Registry, api_client: TelegramApiClient, status_provider: TranscriptionStatusProvider, pipeline_launcher: PipelineLauncher):
        self.settings = settings
        self.registry = registry
        self.api_client = api_client
        self.status_provider = status_provider
        self.pipeline_launcher = pipeline_launcher
        self._stop_event = threading.Event()
        self._consecutive_errors = 0

    def _self_heal_environment(self):
        """Attempts to fix common daemon-level issues like stale locks or DB access."""
        logger.info("Running worker self-healing routine...", action="self_heal_start")
        try:
            # 1. Check DB health
            with sqlite3.connect(self.registry.db_path, timeout=5) as conn:
                conn.execute("SELECT 1")
            
            # 2. Check for zombie transcription processes
            self.status_provider.is_busy() # This triggers lock PID check internally
            
            logger.info("Self-healing: Environment looks OK", action="self_heal_ok")
        except Exception as e:
            logger.error(f"Self-healing failed to restore environment: {e}", action="self_heal_error")

    def run_forever(self):
        logger.info("Task Worker thread started", action="worker_start")
        while not self._stop_event.is_set():
            try:
                # 1. Self-healing busy check
                if self.status_provider.is_busy():
                    time.sleep(15)
                    continue

                # 2. Get next task with error recovery
                try:
                    task = self.registry.get_next_pending_task()
                except sqlite3.Error as db_err:
                    logger.error(f"Database error in worker: {db_err}. Retrying connection...", action="db_error")
                    self._self_heal_environment()
                    time.sleep(10)
                    continue

                if not task:
                    self._consecutive_errors = 0 # Reset on success
                    time.sleep(5)
                    continue

                # 3. Process task
                task_id = task["id"]
                chat_id = task["chat_id"]
                payload = task["payload"]
                task_type = task["task_type"]

                logger.info(f"Worker picked task_id={task_id} type={task_type} payload={payload}", action="worker_pick")
                self.registry.update_task_status(task_id, "processing")
                
                # Notify user - wrapped in try to not crash worker if network is flaky
                try:
                    self.api_client.send_message(chat_id, f"🔄 輪到你了！開始處理：\n{payload}")
                except Exception as net_err:
                    logger.warning(f"Failed to notify user: {net_err}", action="notify_fail")

                if task_type == "url":
                    rc = self.pipeline_launcher.run(chat_id=chat_id, url=payload, wait=True)
                else:
                    rc = self.pipeline_launcher.run(chat_id=chat_id, local_file=Path(payload), wait=True)

                # 4. Update result
                status = "completed" if rc == 0 else "failed"
                self.registry.update_task_status(task_id, status)
                logger.info(f"Task task_id={task_id} finished status={status} rc={rc}", action="worker_finish")
                self._consecutive_errors = 0

            except Exception as e:
                self._consecutive_errors += 1
                logger.error(f"Worker loop error (count={self._consecutive_errors}): {e}", action="worker_error")
                if self._consecutive_errors >= 3:
                    self._self_heal_environment()
                time.sleep(min(60, 10 * self._consecutive_errors))

    def stop(self):
        self._stop_event.set()

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
    def __init__(self, settings: ListenerSettings, api_client: TelegramApiClient, status_provider: TranscriptionStatusProvider, pipeline_launcher: PipelineLauncher, file_downloader: TelegramFileDownloader, registry: Registry, interpreter: MessageInterpreter | None = None):
        self.settings = settings
        self.api_client = api_client
        self.status_provider = status_provider
        self.pipeline_launcher = pipeline_launcher
        self.file_downloader = file_downloader
        self.registry = registry
        self.interpreter = interpreter or MessageInterpreter()

    def handle(self, update: dict) -> None:
        if "message" in update:
            self._handle_message(update["message"])
        elif "callback_query" in update:
            self._handle_callback_query(update["callback_query"])

    def _handle_callback_query(self, callback_query: dict) -> None:
        cb_id = callback_query.get("id")
        if cb_id:
            self.api_client.answer_callback_query(cb_id)

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
            
            # Count pending tasks
            pending_count = 0
            conn = sqlite3.connect(self.registry.db_path)
            try:
                res = conn.execute("SELECT COUNT(*) FROM task_queue WHERE status = 'pending'")
                pending_count = res.fetchone()[0]
            finally:
                conn.close()
            
            queue_msg = f"\n目前排隊中任務：{pending_count}" if pending_count > 0 else ""
            self.api_client.send_message(chat_id, f"目前系統狀態：\n{sys_status}{queue_msg}\n\n{daily_status}")
            return

        if text == "/dump_log":
            log_path = self.settings.base_dir / "logs" / "telegram_listener.log"
            if log_path.exists(): self.api_client.send_document(chat_id, log_path, caption="System Log")
            else: self.api_client.send_message(chat_id, "Log file not found.")
            return

        if text == "/ai_reset":
            self.api_client.send_message(chat_id, "正在重置 AI 對話記憶...")
            try:
                # Starting a new session by NOT using resume
                opencode_bin = os.environ.get("OPENCODE_BIN") or "opencode"
                cmd = [opencode_bin, "run", "-m", "opencode/big-pickle", "--dangerously-skip-permissions", "Hello! This is a fresh session. Please acknowledge and wait for my instructions."]
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
        if urls:
            logger.info(f"URLs extracted count={len(urls)} urls={urls}", action="url_detected")
            
            for url in urls:
                # 1. Check if already processed (Instant return)
                target_dir = telegram_url_output_dir(self.settings.base_dir / "output", url)
                summary_files = sorted(list(target_dir.glob("*.summary.md")), key=lambda p: p.stat().st_mtime, reverse=True)
                
                if summary_files:
                    summary_path = summary_files[0]
                    logger.info(f"Existing summary found for url={url} path={summary_path}", action="cache_hit")
                    
                    title = ""
                    meta_path = target_dir / "metadata.json"
                    if meta_path.exists():
                        try:
                            meta = json.loads(meta_path.read_text(encoding="utf-8"))
                            title = meta.get("title", "")
                        except: pass
                    
                    content = summary_path.read_text(encoding="utf-8")
                    msg = f"此網址已處理過，直接回傳結果：\n\n# {title or '摘要'}\n\n{content}"
                    self.api_client.send_message(chat_id, msg)
                    continue

                # 2. Enqueue task
                task_id = self.registry.enqueue_task("url", url, chat_id)
                pos = self.registry.get_queue_position(task_id)
                
                wait_msg = f"\n目前排在第 {pos} 位。" if pos > 0 else ""
                self.api_client.send_message(chat_id, f"✅ 已收到網址並加入排隊：\n{url}{wait_msg}")

    def _handle_media(self, chat_id: str, media: MediaMessage) -> None:
        safe_name = self.interpreter.safe_filename(media.original_name, media.kind, media.extension)
        task_dir = telegram_media_output_dir(self.settings.base_dir / "output", media.kind, media.original_name)
        dest = task_dir / safe_name
        write_task_metadata(task_dir, {"task_origin": "telegram", "kind": media.kind, "created_at": datetime.now().astimezone().isoformat(), "chat_id": chat_id, "original_name": media.original_name, "file_id": media.file_id, "duration": media.duration})
        
        self.api_client.send_message(chat_id, f"收到媒體：{media.original_name}\n正在下載...")
        if self.file_downloader.download(media.file_id, dest):
            task_id = self.registry.enqueue_task("file", str(dest), chat_id)
            pos = self.registry.get_queue_position(task_id)
            wait_msg = f"\n目前排在第 {pos} 位。" if pos > 0 else ""
            self.api_client.send_message(chat_id, f"✅ 下載完成，已加入排隊。{wait_msg}")
        else:
            self.api_client.send_message(chat_id, "❌ 下載失敗。")

class PipelineHealthMonitor:
    PIPELINE_LOG = BASE_DIR / "launchd_download_and_transcribe.log"
    STATE_FILE = BASE_DIR / "launchd_state" / "pipeline_health.json"

    def __init__(self, send_alert_fn):
        self.send_alert = send_alert_fn
        self.state = self._load()
        self.iteration = 0

    def _load(self) -> dict:
        try:
            if self.STATE_FILE.exists():
                return json.loads(self.STATE_FILE.read_text())
        except Exception:
            pass
        return {"last_alert": 0, "consecutive_failures": 0}

    def _save(self):
        self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.STATE_FILE.write_text(json.dumps(self.state, ensure_ascii=False, indent=2))

    def check(self):
        self.iteration += 1
        if self.iteration % 300 != 0:
            return
        now = time.time()
        if not self.PIPELINE_LOG.exists():
            return
        try:
            result = subprocess.run(["tail", "-30", str(self.PIPELINE_LOG)], capture_output=True, text=True, timeout=10)
            tail_output = result.stdout
        except Exception:
            return
        if not tail_output:
            return
        lines = tail_output.splitlines()
        pipeline_succeeded = any("Pipeline finished overall_ok=True" in line for line in lines)
        if pipeline_succeeded:
            self.state["consecutive_failures"] = 0
            self._save()
            return

        _YTDLP_KEYWORDS = {"yt-dlp", "youtube", "members-only", "geo-restricted", "private video"}
        has_error = False
        error_lines_raw = []
        for line in lines:
            lower = line.lower()
            if any(kw in lower for kw in ["traceback", "modulenotfounderror", "importerror", "exception:", "crash"]):
                has_error = True
                error_lines_raw.append(line.strip())
            elif "error:" in lower or "failed" in lower:
                if any(kw in lower for kw in _YTDLP_KEYWORDS):
                    continue
                has_error = True
                error_lines_raw.append(line.strip())

        if has_error:
            self.state["consecutive_failures"] = self.state.get("consecutive_failures", 0) + 1
        else:
            self.state["consecutive_failures"] = 0

        if self.state["consecutive_failures"] >= 3 and now - self.state.get("last_alert", 0) > 3600:
            error_lines = error_lines_raw[:8]
            msg = f"⚠️ Pipeline 健康檢查異常\n連續 {self.state['consecutive_failures']} 次排程執行失敗\n\n最近錯誤：\n" + "\n".join(error_lines) + f"\n\n檢查日誌：{self.PIPELINE_LOG}"
            logger.warning(f"Pipeline health alert triggered", action="pipeline_alert")
            self.send_alert(msg)
            self.state["last_alert"] = now
        self._save()


class TelegramPoller:
    def __init__(self, api_client: TelegramApiClient, update_handler: TelegramUpdateHandler, health_monitor: PipelineHealthMonitor | None = None, owner_chat_id: str | int | None = None):
        self.api_client = api_client
        self.update_handler = update_handler
        self.health_monitor = health_monitor
        self.owner_chat_id = owner_chat_id
        self.last_update_id = 0
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
            try:
                self.poll_once()
                if self.health_monitor:
                    self.health_monitor.check()
                if self.owner_chat_id:
                    self.api_client.send_error_report_if_needed(self.owner_chat_id)
                time.sleep(1)
            except KeyboardInterrupt: break
            except Exception as e: logger.error(f"Loop error error=\"{e}\"", action="poller_error"); time.sleep(10)

def build_settings() -> ListenerSettings:
    load_project_env(BASE_DIR)
    # Prefer the venv python if it exists to ensure dependencies like pydantic are available
    python_exe = sys.executable
    venv_python = BASE_DIR / "venv" / "bin" / "python3"
    if venv_python.exists():
        python_exe = str(venv_python)
        
    return ListenerSettings(BASE_DIR, os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID"), python_exe)

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
    
    registry = Registry(settings.base_dir / "tasks.db")
    status_provider = TranscriptionStatusProvider()
    pipeline_launcher = PipelineLauncher(settings.base_dir, settings.python_executable)
    
    # Start background worker
    worker = TaskWorker(settings, registry, api_client, status_provider, pipeline_launcher)
    worker_thread = threading.Thread(target=worker.run_forever, daemon=True)
    worker_thread.start()
    
    handler = TelegramUpdateHandler(
        settings, api_client, status_provider, 
        pipeline_launcher, TelegramFileDownloader(api_client, settings.bot_token),
        registry
    )
    
    health_monitor = PipelineHealthMonitor(lambda msg: api_client.send_message(settings.owner_chat_id, msg))
    poller = TelegramPoller(api_client, handler, health_monitor, settings.owner_chat_id)
    logger.info("Telegram Poller started", action="poller_start")
    
    try:
        poller.run_forever()
    except KeyboardInterrupt:
        logger.info("Listener stopping...", action="shutdown")
        worker.stop()
        
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
