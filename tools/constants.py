"""
Centralized constants for the WhisperSummarize-Flow project.
Provides a single source of truth for string literals and categories.
"""

class TaskKind:
    YOUTUBE = "youtube"
    PODCAST = "podcast"
    TELEGRAM_AUDIO = "telegram_audio"
    TELEGRAM_VIDEO = "telegram_video"
    TELEGRAM_YOUTUBE = "telegram_youtube"
    LOCAL_FILE = "local_file"

class SourceType:
    YOUTUBE = "youtube"
    PODCAST = "podcast"
    RSS = "rss"

class TranscriberEngine:
    WHISPERKIT = "whisperkit"
    WHISPERCPP = "whispercpp"

class LogLabels:
    OK = "[OK]"
    FAILED = "[FAILED]"
    SKIPPED = "[SKIPPED]"
    STATUS = "[Status]"
    EVENT = "[Event]"
