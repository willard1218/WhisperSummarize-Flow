#!/bin/bash

# Configuration
BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_NAME="com.whispersummarize.listener.plist"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME"
TEMPLATE_PATH="$BASE_DIR/launchd_state/$PLIST_NAME.template"
PYTHON_BIN="$(which python3)"
SCRIPT_PATH="$BASE_DIR/tools/telegram_listener.py"

echo "Setting up Telegram Listener background service..."

# Create the specific plist from template
sed "s|PYTHON_BIN_PATH|$PYTHON_BIN|g; s|SCRIPT_PATH|$SCRIPT_PATH|g; s|WORKING_DIR|$BASE_DIR|g" \
    "$TEMPLATE_PATH" > "$PLIST_PATH"

# Load the service
launchctl unload "$PLIST_PATH" 2>/dev/null
launchctl load "$PLIST_PATH"

echo "Service loaded: $PLIST_NAME"
echo "Log files are located in $BASE_DIR/launchd_state/"
launchctl list | grep whispersummarize.listener
