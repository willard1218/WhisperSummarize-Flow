#!/bin/bash

# Copy to local_config.sh and adjust for this machine.

# Provide absolute paths if the executables are not in your system PATH
# GENSRT_SCRIPT="/path/to/gensrt.sh"
# PYTHON_BIN="/usr/bin/python3"
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
