#!/bin/bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 HOUR MINUTE"
  echo "Example: $0 16 11"
  exit 1
fi

hour="$1"
minute="$2"

if ! [[ "$hour" =~ ^[0-9]+$ && "$minute" =~ ^[0-9]+$ ]]; then
  echo "Hour and minute must be integers."
  exit 1
fi

if (( hour < 0 || hour > 23 || minute < 0 || minute > 59 )); then
  echo "Hour must be 0-23 and minute must be 0-59."
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
base_dir="$(cd "$script_dir/.." && pwd)"
source_plist="$base_dir/launchd/com.willard.soundon-rss-daily.plist"
target_plist="$HOME/Library/LaunchAgents/com.willard.soundon-rss-daily.plist"
runner_path="$base_dir/launchd/run_soundon_daily.sh"
label="com.willard.soundon-rss-daily"
uid="$(id -u)"

/usr/bin/python3 - "$source_plist" "$target_plist" "$runner_path" "$hour" "$minute" <<'PY'
import plistlib
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
runner_path = sys.argv[3]
hour = int(sys.argv[4])
minute = int(sys.argv[5])

data = {
    "Label": "com.willard.soundon-rss-daily",
    "ProgramArguments": ["/bin/bash", runner_path],
    "RunAtLoad": False,
    "StartCalendarInterval": {"Hour": hour, "Minute": minute},
}

for path in (source_path, target_path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        plistlib.dump(data, f, sort_keys=False)
PY

launchctl bootout "gui/$uid" "$target_plist" 2>/dev/null || true
launchctl bootstrap "gui/$uid" "$target_plist"

echo "Updated $label to run daily at $(printf '%02d:%02d' "$hour" "$minute")."
launchctl print "gui/$uid/$label" | sed -n '1,80p'
