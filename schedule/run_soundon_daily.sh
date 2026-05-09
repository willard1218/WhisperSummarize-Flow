#!/bin/bash
set -euo pipefail

if [[ "${RUN_UNDER_CAFFEINATE:-0}" != "1" ]]; then
  exec /usr/bin/caffeinate -dimsu env RUN_UNDER_CAFFEINATE=1 /bin/bash "$0" "$@"
fi

RUN_TTY="$(/usr/bin/tty 2>/dev/null || true)"
close_own_terminal_window() {
  local status=$?
  if [[ "${CLOSE_TERMINAL_ON_DONE:-1}" == "1" && "$RUN_TTY" == /dev/ttys* ]]; then
    /bin/bash -c 'sleep 1; /usr/bin/osascript - "$1" >/dev/null 2>&1 <<'"'"'APPLESCRIPT'"'"'
on run argv
  set targetTty to item 1 of argv
  tell application "Terminal"
    repeat with terminalWindow in windows
      repeat with terminalTab in tabs of terminalWindow
        if tty of terminalTab is targetTty then
          if (count of tabs of terminalWindow) is 1 then
            close terminalWindow
          else
            close terminalTab
          end if
          return
        end if
      end repeat
    end repeat
  end tell
end run
APPLESCRIPT
' _ "$RUN_TTY" >/dev/null 2>&1 &
  fi
  exit "$status"
}
trap close_own_terminal_window EXIT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
LOCAL_CONFIG_FILE="$BASE_DIR/config/local_config.sh"
if [[ -f "$LOCAL_CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$LOCAL_CONFIG_FILE"
fi
if [[ -z "${FFMPEG_BIN_DIR:-}" && -x "$HOME/bin/JDownloader 2.0/tools/mac/ffmpeg_10.10+/ffmpeg" ]]; then
  FFMPEG_BIN_DIR="$HOME/bin/JDownloader 2.0/tools/mac/ffmpeg_10.10+"
fi
if [[ -n "${FFMPEG_BIN_DIR:-}" ]]; then
  export PATH="$FFMPEG_BIN_DIR:$PATH"
fi
export GENSRT_SCRIPT="${GENSRT_SCRIPT:-gensrt.sh}"
export PYTHON_BIN="${PYTHON_BIN:-python3}"
export RECIPIENT_CONFIG_FILE="${RECIPIENT_CONFIG_FILE:-$BASE_DIR/config/recipient_groups.local.json}"
export OPENCC_TRADITIONALIZE="${OPENCC_TRADITIONALIZE:-0}"
export OPENCC_CONFIG="${OPENCC_CONFIG:-s2twp.json}"

RUN_DATE="${1:-$(date '+%Y-%m-%d')}"
OUTPUT_DIR="${2:-$BASE_DIR/output}"
TRANSCRIBE_SCRIPT="$GENSRT_SCRIPT"
PODCAST_CONFIG_FILE="${3:-$BASE_DIR/config/subscriptions.json}"
YOUTUBE_CONFIG_FILE="${4:-$BASE_DIR/config/youtube_subscriptions.json}"
RECIPIENT_CONFIG_FILE="${5:-$RECIPIENT_CONFIG_FILE}"
DAILY_RUNNER="$BASE_DIR/pipeline/run_daily_pipeline.py"
PYTHON_BIN="$PYTHON_BIN"
LOG_FILE="$BASE_DIR/launchd_download_and_transcribe.log"
STATE_DIR="$BASE_DIR/launchd_state"
DONE_MARKER="$STATE_DIR/$RUN_DATE.all-downloaded"
mkdir -p "$OUTPUT_DIR"
mkdir -p "$STATE_DIR"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "Python 3 executable not found."
  exit 127
fi

if [[ -f "$DONE_MARKER" ]]; then
  {
    echo "==== $(date '+%Y-%m-%d %H:%M:%S %Z') start ===="
    echo "Run date $RUN_DATE already completed earlier. Skipping."
    echo "==== $(date '+%Y-%m-%d %H:%M:%S %Z') done ===="
  } >>"$LOG_FILE" 2>&1
  exit 0
fi

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S %Z') start ===="
  echo "Using Python: $PYTHON_BIN"
  overall_status=0
  set +e
  pipeline_output="$("$PYTHON_BIN" "$DAILY_RUNNER" --date "$RUN_DATE" --output-root "$OUTPUT_DIR" --transcribe-script "$TRANSCRIBE_SCRIPT" --podcast-config "$PODCAST_CONFIG_FILE" --youtube-config "$YOUTUBE_CONFIG_FILE" --recipient-config "$RECIPIENT_CONFIG_FILE" 2>&1)"
  pipeline_status=$?
  set -e
  printf '%s\n' "$pipeline_output"
  if [[ "$pipeline_output" == *"PIPELINE_ALL_DOWNLOADED=1"* ]]; then
    : >"$DONE_MARKER"
    echo "Created done marker: $DONE_MARKER"
  fi
  if [[ $pipeline_status -eq 0 ]]; then
    echo "Daily pipeline: done"
  else
    overall_status=1
    echo "Daily pipeline: completed with partial failures"
  fi
  echo "==== $(date '+%Y-%m-%d %H:%M:%S %Z') done ===="
  exit "$overall_status"
} >>"$LOG_FILE" 2>&1
