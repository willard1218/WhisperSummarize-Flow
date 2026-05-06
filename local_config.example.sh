#!/bin/bash

# Copy to local_config.sh and adjust for this machine.

GENSRT_SCRIPT="$HOME/Developer/gensrt.sh"
PYTHON_BIN="/opt/homebrew/bin/python3"
OPENCC_TRADITIONALIZE="1"
OPENCC_CONFIG="s2twp.json"
DEBUG_RECIPIENT="yourname@example.com"

# SMTP Settings (iCloud Example)
SMTP_HOST="smtp.mail.me.com"
SMTP_PORT="587"
SMTP_USER="yourname@icloud.com"
SMTP_PASS="aaaa-bbbb-cccc-dddd"  # Use App-Specific Password here
SMTP_FROM="yourname@icloud.com"

# Optional overrides:
# RECIPIENT_CONFIG_FILE="$HOME/path/to/soundon_rss/recipient_groups.local.json"
