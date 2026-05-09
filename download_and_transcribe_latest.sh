#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_CONFIG_FILE="$SCRIPT_DIR/local_config.sh"
if [[ -f "$LOCAL_CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$LOCAL_CONFIG_FILE"
fi

CHANNEL_URL="${1:-}"
if [[ -z "$CHANNEL_URL" ]]; then
  echo "Usage: $0 <CHANNEL_URL> [OUTPUT_DIR] [MAIL_RECIPIENTS] [TRANSCRIBE_SCRIPT]" >&2
  exit 1
fi
OUTPUT_DIR="${2:-$SCRIPT_DIR/output}"
MAIL_RECIPIENTS_RAW="${3:-}"
TRANSCRIBE_SCRIPT="${4:-${GENSRT_SCRIPT:-gensrt.sh}}"
ARCHIVE_FILE="$SCRIPT_DIR/id.txt"
SKIP_MAIL="${SKIP_MAIL:-0}"

mkdir -p "$OUTPUT_DIR"

if ! command -v yt-dlp >/dev/null 2>&1; then
  echo "yt-dlp not found in PATH" >&2
  exit 127
fi

if [[ ! -x "$TRANSCRIBE_SCRIPT" ]]; then
  echo "Transcribe script not found or not executable: $TRANSCRIBE_SCRIPT" >&2
  exit 127
fi

OPENCC_TRADITIONALIZE="${OPENCC_TRADITIONALIZE:-0}"
OPENCC_CONFIG="${OPENCC_CONFIG:-s2twp.json}"
CONVERTER_SCRIPT="$SCRIPT_DIR/convert_transcript_opencc.py"

if [[ "$OPENCC_TRADITIONALIZE" == "1" && ! -f "$CONVERTER_SCRIPT" ]]; then
  echo "OpenCC converter script not found: $CONVERTER_SCRIPT" >&2
  exit 127
fi

IFS=',' read -r -a MAIL_RECIPIENTS <<<"$MAIL_RECIPIENTS_RAW"
if [[ -z "$MAIL_RECIPIENTS_RAW" || ${#MAIL_RECIPIENTS[@]} -eq 0 ]]; then
  echo "No mail recipients provided." >&2
  exit 1
fi

latest_entry="$(yt-dlp \
  --flat-playlist \
  --playlist-end 1 \
  --match-filter "live_status=was_live" \
  --print-json \
  "$CHANNEL_URL")"

if [[ -z "$latest_entry" ]]; then
  echo "No completed livestream found at $CHANNEL_URL" >&2
  exit 1
fi

video_id="$(/usr/bin/python3 -c 'import json,sys; data=json.loads(sys.stdin.read()); print(data.get("id",""))' <<<"$latest_entry")"
video_title="$(/usr/bin/python3 -c 'import json,sys; data=json.loads(sys.stdin.read()); print(data.get("title",""))' <<<"$latest_entry")"
video_url="$(/usr/bin/python3 -c 'import json,sys; data=json.loads(sys.stdin.read()); print(data.get("webpage_url") or data.get("url",""))' <<<"$latest_entry")"

if [[ -z "$video_id" || -z "$video_url" ]]; then
  echo "Could not resolve the latest livestream video." >&2
  exit 1
fi

audio_path="$(find "$OUTPUT_DIR" -maxdepth 1 -type f -name "*__${video_id}.mp3" -print -quit)"

if [[ -z "$audio_path" ]]; then
  yt-dlp \
    --download-archive "$ARCHIVE_FILE" \
    --no-overwrites \
    -f "bestaudio/best" \
    -x \
    --audio-format mp3 \
    --paths "$OUTPUT_DIR" \
    -o "%(title).200B__%(id)s.%(ext)s" \
    "$video_url"

  audio_path="$(find "$OUTPUT_DIR" -maxdepth 1 -type f -name "*__${video_id}.mp3" -print -quit)"
fi

if [[ -z "$audio_path" || ! -f "$audio_path" ]]; then
  echo "Downloaded mp3 not found for video $video_id" >&2
  exit 1
fi

"$TRANSCRIBE_SCRIPT" "$audio_path"

audio_base="${audio_path%.mp3}"
srt_txt_path="${audio_base}.srt.txt"
txt_path="${audio_base}.txt"

if [[ -f "$srt_txt_path" ]]; then
  attachment_path="$srt_txt_path"
elif [[ -f "$txt_path" ]]; then
  attachment_path="$txt_path"
else
  echo "Transcript not found for $audio_path" >&2
  exit 1
fi

if [[ "$OPENCC_TRADITIONALIZE" == "1" ]]; then
  if [[ "$attachment_path" == *.srt.txt ]]; then
    traditional_path="${attachment_path%.srt.txt}.zh-Hant.srt.txt"
    echo "Converting to Traditional Chinese: $(basename "$traditional_path")"
    if /usr/bin/python3 "$CONVERTER_SCRIPT" "$attachment_path" --output-path "$traditional_path" --config "$OPENCC_CONFIG"; then
      attachment_path="$traditional_path"
    else
      echo "Traditional Chinese conversion failed; using original transcript." >&2
    fi
    if [[ -f "$txt_path" ]]; then
      traditional_txt_path="${txt_path%.txt}.zh-Hant.txt"
      echo "Converting to Traditional Chinese: $(basename "$traditional_txt_path")"
      /usr/bin/python3 "$CONVERTER_SCRIPT" "$txt_path" --output-path "$traditional_txt_path" --config "$OPENCC_CONFIG" || true
    fi
  elif [[ "$attachment_path" == *.txt ]]; then
    traditional_path="${attachment_path%.txt}.zh-Hant.txt"
    echo "Converting to Traditional Chinese: $(basename "$traditional_path")"
    if /usr/bin/python3 "$CONVERTER_SCRIPT" "$attachment_path" --output-path "$traditional_path" --config "$OPENCC_CONFIG"; then
      attachment_path="$traditional_path"
    else
      echo "Traditional Chinese conversion failed; using original transcript." >&2
    fi
  fi
fi

if [[ "$SKIP_MAIL" == "1" ]]; then
  echo "Processed: $audio_path"
  echo "Transcript: $attachment_path"
  echo "SKIP_MAIL=1, not sending email."
  exit 0
fi

# 移除舊的 AppleScript 邏輯，改為由呼叫者處理寄信，或是顯示提示
echo "Mailing should now be handled by Python scripts to avoid opening Mail.app."
echo "Processed: $audio_path"
echo "Transcript: $attachment_path"
exit 0
