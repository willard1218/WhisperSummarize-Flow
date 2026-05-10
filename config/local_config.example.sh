#!/bin/bash

# Copy to local_config.sh and adjust for this machine.

# Provide absolute paths if the executables are not in your system PATH
# GENSRT_SCRIPT="/path/to/gensrt.sh"
# PYTHON_BIN="/usr/bin/python3"
# FFMPEG_BIN_DIR="/opt/homebrew/bin"
# Pipeline Stage Toggles (1 to enable, 0 to disable)
ENABLE_TRANSCRIBE="1"
ENABLE_TRADITIONALIZE="1"  # Simplified Chinese to Traditional Chinese (OpenCC)
ENABLE_SUMMARIZE="1"
ENABLE_OLLAMA="0"          # Use local Ollama for summarization (requires ENABLE_SUMMARIZE="1")
SUMMARIZE_POLICY="first"   # 'first' to stop at first success, 'all' to run all models for comparison
ENABLE_MAIL="1"
ENABLE_TELEGRAM="1"

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
