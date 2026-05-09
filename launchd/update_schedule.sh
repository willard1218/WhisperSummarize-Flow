#!/bin/bash
set -euo pipefail

if [[ $# -eq 2 ]]; then
  mode="single"
  hour="$1"
  minute="$2"
elif [[ $# -eq 3 ]]; then
  mode="hourly_range"
  start_hour="$1"
  end_hour="$2"
  minute="$3"
else
  echo "Usage: $0 HOUR MINUTE"
  echo "       $0 START_HOUR END_HOUR MINUTE"
  echo "Example: $0 16 11"
  echo "Example: $0 15 23 00"
  exit 1
fi

for value in "${hour:-}" "${start_hour:-}" "${end_hour:-}" "$minute"; do
  if [[ -n "$value" && ! "$value" =~ ^[0-9]+$ ]]; then
    echo "Hours and minute must be integers."
    exit 1
  fi
done

if [[ "$mode" == "single" ]]; then
  if (( hour < 0 || hour > 23 || minute < 0 || minute > 59 )); then
    echo "Hour must be 0-23 and minute must be 0-59."
    exit 1
  fi
else
  if (( start_hour < 0 || start_hour > 23 || end_hour < 0 || end_hour > 23 || minute < 0 || minute > 59 )); then
    echo "Start/end hour must be 0-23 and minute must be 0-59."
    exit 1
  fi
  if (( start_hour > end_hour )); then
    echo "START_HOUR must be less than or equal to END_HOUR."
    exit 1
  fi
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
base_dir="$(cd "$script_dir/.." && pwd)"
source_plist="$base_dir/launchd/com.willard.soundon-rss-daily.plist"
target_plist="$HOME/Library/LaunchAgents/com.willard.soundon-rss-daily.plist"
runner_path="$base_dir/launchd/run_soundon_daily.sh"
label="com.willard.soundon-rss-daily"
uid="$(id -u)"

/usr/bin/python3 - "$source_plist" "$target_plist" "$runner_path" "$mode" "${hour:-}" "${start_hour:-}" "${end_hour:-}" "$minute" <<'PY'
import plistlib
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
runner_path = sys.argv[3]
mode = sys.argv[4]
hour = int(sys.argv[5]) if sys.argv[5] else None
start_hour = int(sys.argv[6]) if sys.argv[6] else None
end_hour = int(sys.argv[7]) if sys.argv[7] else None
minute = int(sys.argv[8])

if mode == "single":
    start_calendar_interval = {"Hour": hour, "Minute": minute}
else:
    start_calendar_interval = [
        {"Hour": current_hour, "Minute": minute}
        for current_hour in range(start_hour, end_hour + 1)
    ]

data = {
    "Label": "com.willard.soundon-rss-daily",
    "ProgramArguments": ["/bin/bash", runner_path],
    "RunAtLoad": False,
    "StartCalendarInterval": start_calendar_interval,
}

for path in (source_path, target_path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        plistlib.dump(data, f, sort_keys=False)
PY

launchctl bootout "gui/$uid" "$target_plist" 2>/dev/null || true
launchctl bootstrap "gui/$uid" "$target_plist"

if [[ "$mode" == "single" ]]; then
  echo "Updated $label to run daily at $(printf '%02d:%02d' "$hour" "$minute")."
else
  echo "Updated $label to run hourly from $(printf '%02d:%02d' "$start_hour" "$minute") to $(printf '%02d:%02d' "$end_hour" "$minute")."
fi
launchctl print "gui/$uid/$label" | sed -n '1,80p'
