#!/bin/bash

# Copy to local_config.sh and adjust for this machine.

# Provide absolute paths if the executables are not in your system PATH
# GENSRT_SCRIPT="/path/to/gensrt.sh"
# PYTHON_BIN="/usr/bin/python3"
# FFMPEG_BIN_DIR="/opt/homebrew/bin"
OPENCC_TRADITIONALIZE="1"
OPENCC_CONFIG="s2twp.json"
DEBUG_RECIPIENT="yourname@example.com"

# Pipeline Stage Toggles (1 to enable, 0 to disable)
ENABLE_TRANSCRIBE="1"
ENABLE_TRADITIONALIZE="1"
ENABLE_SUMMARIZE="1"
ENABLE_MAIL="1"

# SMTP Settings (iCloud Example)
SMTP_HOST="smtp.mail.me.com"
SMTP_PORT="587"
SMTP_USER="yourname@icloud.com"
SMTP_PASS="aaaa-bbbb-cccc-dddd"  # Use App-Specific Password here
SMTP_FROM="yourname@icloud.com"

# Optional overrides:
# RECIPIENT_CONFIG_FILE="$HOME/path/to/WhisperSummarize-Flow/config/recipient_groups.local.json"
