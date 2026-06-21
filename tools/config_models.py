from pydantic import BaseModel, Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional
from enum import Enum

class TranscriberType(str, Enum):
    WHISPERKIT = "whisperkit"
    WHISPERCPP = "whispercpp"

class TaskOrigin(str, Enum):
    DEFAULT = "default"
    TELEGRAM = "telegram"

class Subscription(BaseModel):
    name: str
    url: str
    recipient_group: str = "default"
    enabled: bool = True

class YouTubeSubscription(Subscription):
    handle: str

class RecipientGroup(BaseModel):
    emails: List[str] = []
    telegram_chat_ids: List[int] = []

class AppConfig(BaseSettings):
    # Core paths
    output_root: str
    transcribe_script: str
    
    # Tool paths
    yt_dlp_bin: str
    ffmpeg_bin: str
    ffprobe_bin: str
    opencc_bin: str
    whisperkit_bin: str
    
    # AI Config
    gemini_api_key: Optional[str] = None
    
    # Telegram Config
    telegram_bot_token: Optional[str] = None
    telegram_authorized_chats: List[str] = []
    telegram_chat_id: Optional[str] = None
    
    # Mail Config
    smtp_server: str
    smtp_port: int
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    mail_sender: str
    
    # Pipeline Config
    default_concurrency: int
    default_transcriber: TranscriberType
    enable_transcribe: bool = True
    enable_traditionalize: bool
    enable_summarize: bool = True
    enable_mail: bool = True
    enable_telegram: bool = True
    opencc_config: str
    
    # Capacity Config
    max_output_daily_bytes: int
    max_output_telegram_bytes: int
    
    model_config = SettingsConfigDict(
        env_prefix="WS_",
        env_file=None,
        extra="ignore"
    )
