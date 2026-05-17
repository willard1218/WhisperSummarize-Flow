from pydantic import BaseModel, Field, HttpUrl
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

class AppConfig(BaseModel):
    # Core paths
    output_root: str = "output"
    transcribe_script: str = "gensrt.sh"
    
    # AI Config
    gemini_api_key: Optional[str] = None
    
    # Telegram Config
    telegram_bot_token: Optional[str] = None
    telegram_authorized_chats: List[str] = []
    
    # Mail Config
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    mail_sender: str = "WhisperSummarize <noreply@example.com>"
    
    # Pipeline Config
    default_concurrency: int = 4
    default_transcriber: TranscriberType = TranscriberType.WHISPERKIT
    
    class Config:
        env_prefix = "WS_"
