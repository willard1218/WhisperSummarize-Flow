#!/bin/bash

# Copy to local_config.sh and adjust for this machine.

# Provide absolute paths if the executables are not in your system PATH
# GENSRT_SCRIPT="/path/to/gensrt.sh"
# PYTHON_BIN="/usr/bin/python3"
# FFMPEG_BIN_DIR="/opt/homebrew/bin"
# OPENCC_BIN="/opt/homebrew/bin/opencc"
# Pipeline Stage Toggles (1 to enable, 0 to disable)
ENABLE_TRANSCRIBE="1"
ENABLE_TRADITIONALIZE="1"  # Simplified Chinese to Traditional Chinese (OpenCC)
ENABLE_SUMMARIZE="1"
ENABLE_OLLAMA="0"          # Optional fallback: use local Ollama for summarization
ENABLE_OPENCODE="0"        # Optional fallback: use OpenCode CLI for summarization
ENABLE_MAIL="1"
ENABLE_TELEGRAM="1"

# Gemini API summarization (required when ENABLE_SUMMARIZE="1")
GEMINI_API_KEY="your-gemini-api-key"
GEMINI_MODEL="gemini-flash-latest"
GEMINI_TIMEOUT_SECONDS="300"

# OpenCC Settings
OPENCC_CONFIG="s2twp.json"

# Debug / Testing
DEBUG_RECIPIENT="yourname@example.com"

# SMTP Settings (iCloud Example)
SMTP_HOST="smtp.mail.me.com"
SMTP_PORT="587"
SMTP_USER="yourname@icloud.com"
SMTP_PASS="aaaa-bbbb-cccc-dddd"  # Use App-Specific Password here
SMTP_FROM="yourname@icloud.com"

# Telegram Notifications (Example values below)
# TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
# TELEGRAM_CHAT_ID="987654321"
# DEBUG_TELEGRAM_CHAT_ID="987654321" # Same as above if you want to test to yourself

# Optional overrides:
# RECIPIENT_CONFIG_FILE="$HOME/path/to/WhisperSummarize-Flow/config/recipient_groups.local.json"

# Binary Paths (Required)
WS_YT_DLP_BIN="yt-dlp"
WS_FFMPEG_BIN="ffmpeg"
WS_FFPROBE_BIN="ffprobe"
WS_OPENCC_BIN="opencc"
WS_WHISPERKIT_BIN="whisperkit-cli"

# Output Capacity Limits (in bytes, default 5GB)
WS_MAX_OUTPUT_DAILY_BYTES="5368709120"
WS_MAX_OUTPUT_TELEGRAM_BYTES="5368709120"

# Pipeline Settings (Required)
WS_OUTPUT_ROOT="output"
WS_TRANSCRIBE_SCRIPT="gensrt.sh"
WS_DEFAULT_CONCURRENCY="4"
WS_DEFAULT_TRANSCRIBER="whisperkit"
WS_ENABLE_TRADITIONALIZE="True"
WS_OPENCC_CONFIG="s2twp.json"
