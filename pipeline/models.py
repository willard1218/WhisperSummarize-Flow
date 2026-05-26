from __future__ import annotations

import fcntl
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List

from tools.logger import TaskLogger


@dataclass
class DailyItem:
    label: str
    kind: str
    source_url: str
    emails: list[str]
    output_dir: Path
    prompt_file: Path | None = None
    audio_path: Path | None = None
    transcript_path: Path | None = None
    mail_attachment_path: Path | None = None
    mail_body: str = ""
    title: str = ""
    duration_str: str = ""
    processing_time_str: str = ""
    summarization_time_str: str = ""
    failed: bool = False
    download_ready: bool = False
    messages: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def log_trace(self, message: str) -> None:
        self.trace.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")


class PipelineContext:
    def __init__(self, args, items: List[DailyItem]):
        self.args = args
        self.items = items
        self.overall_ok = True
        self.task_loggers = [TaskLogger("pipeline", item.label) for item in items]

    def report_status(self, item_index: int, message: str, level: str = "info") -> None:
        task_logger = self.task_loggers[item_index]
        if level == "error":
            task_logger.error(message)
            self.overall_ok = False
        else:
            task_logger.info(message)

    def log_event(self, item_index: int, action: str, status: str, duration: float = 0, detail: str = "") -> None:
        msg = f"EVENT {action} status={status} duration={duration:.2f}s"
        if detail:
            msg += f" detail=\"{detail}\""
        self.task_loggers[item_index].info(msg, action=action)


class TranscriptionLock:
    """A file-based lock to ensure only one transcription runs at a time across processes and threads."""

    def __init__(self, lock_file: str = "/tmp/whisper_transcription.lock"):
        self.lock_file_path = Path(lock_file)
        self.lock_file = None
        self.thread_lock = threading.Lock()

    def __enter__(self):
        self.thread_lock.acquire()
        try:
            self.lock_file = open(self.lock_file_path, "w")
            fcntl.flock(self.lock_file, fcntl.LOCK_EX)
            self.lock_file.write(str(os.getpid()))
            self.lock_file.flush()
        except Exception:
            self.thread_lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.lock_file:
                fcntl.flock(self.lock_file, fcntl.LOCK_UN)
                self.lock_file.close()
                self.lock_file = None
        finally:
            self.thread_lock.release()
