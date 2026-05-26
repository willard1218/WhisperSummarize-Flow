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
  echo "       $0 START_HOUR END_HOUR MINUTE[,MINUTE...]"
  echo "Example: $0 16 11"
  echo "Example: $0 15 23 00"
  echo "Example: $0 15 23 00,30"
  exit 1
fi

for value in "${hour:-}" "${start_hour:-}" "${end_hour:-}"; do
  if [[ -n "$value" && ! "$value" =~ ^[0-9]+$ ]]; then
    echo "Hours must be integers."
    exit 1
  fi
done

if [[ ! "$minute" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
  echo "Minute must be an integer or comma-separated integers (e.g. 00,30)."
  exit 1
fi

IFS=',' read -r -a minute_values <<< "$minute"
for m in "${minute_values[@]}"; do
  if (( m < 0 || m > 59 )); then
    echo "Minute values must be 0-59."
    exit 1
  fi
done

if [[ "$mode" == "single" ]]; then
  if (( hour < 0 || hour > 23 )); then
    echo "Hour must be 0-23 and minute must be 0-59."
    exit 1
  fi
else
  if (( start_hour < 0 || start_hour > 23 || end_hour < 0 || end_hour > 23 )); then
    echo "Start/end hour must be 0-23 and minute must be 0-59."
    exit 1
  fi
  if (( start_hour > end_hour )); then
    echo "START_HOUR must be less than or equal to END_HOUR."
    exit 1
  fi
fi
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
base_dir="$(dirname "$script_dir")"

source_plist="$base_dir/schedule/com.whispersummarize.daily.plist"
target_plist="$HOME/Library/LaunchAgents/com.whispersummarize.daily.plist"
runner_path="$base_dir/schedule/run_soundon_daily.sh"

label="com.whispersummarize.daily"
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
minutes = [int(v) for v in sys.argv[8].split(",")]

if mode == "single":
    if len(minutes) == 1:
        start_calendar_interval = {"Hour": hour, "Minute": minutes[0]}
    else:
        start_calendar_interval = [
            {"Hour": hour, "Minute": minute}
            for minute in sorted(set(minutes))
        ]
else:
    start_calendar_interval = [
        {"Hour": current_hour, "Minute": minute}
        for current_hour in range(start_hour, end_hour + 1)
        for minute in sorted(set(minutes))
    ]

data = {
    "Label": "com.whispersummarize.daily",
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
  echo "Updated $label to run daily at hour $(printf '%02d' "$hour") minute(s) [$minute]."
else
  echo "Updated $label to run hourly from $(printf '%02d:00' "$start_hour") to $(printf '%02d:59' "$end_hour") at minute(s) [$minute]."
fi
launchctl print "gui/$uid/$label" | sed -n '1,80p'
