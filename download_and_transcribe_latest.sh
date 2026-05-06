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
  else
    echo "Skipping Traditional Chinese conversion for non-.srt transcript: $(basename "$attachment_path")" >&2
  fi
fi

if [[ "$SKIP_MAIL" == "1" ]]; then
  echo "Processed: $audio_path"
  echo "Transcript: $attachment_path"
  echo "SKIP_MAIL=1, not sending email."
  exit 0
fi

mail_success=1
for recipient in "${MAIL_RECIPIENTS[@]}"; do
  recipient="$(printf '%s' "$recipient" | xargs)"
  if [[ -z "$recipient" ]]; then
    continue
  fi

  recipient_hash="$(printf '%s' "$recipient" | shasum | awk '{print substr($1,1,12)}')"
  mail_marker="${attachment_path}.${recipient_hash}.mail-sent"

  if [[ -f "$mail_marker" ]]; then
    echo "Already sent to $recipient: $(basename "$attachment_path")"
    continue
  fi

  if /usr/bin/osascript - "$recipient" "$video_title" "$attachment_path" <<'APPLESCRIPT'
on run argv
  set recipientAddress to item 1 of argv
  set videoTitle to item 2 of argv
  set attachmentPath to POSIX file (item 3 of argv)
  set subjectText to "YouTube transcript " & videoTitle

  tell application "Mail"
    activate
    set newMessage to make new outgoing message with properties {subject:subjectText, content:"", visible:false}
    tell newMessage
      make new to recipient at end of to recipients with properties {address:recipientAddress}
      make new attachment with properties {file name:attachmentPath} at after the last paragraph
      send
    end tell
  end tell
end run
APPLESCRIPT
  then
    touch "$mail_marker"
    echo "Sent to: $recipient"
  else
    mail_success=0
    echo "Failed to send to: $recipient" >&2
  fi
done

echo "Processed: $audio_path"
echo "Transcript: $attachment_path"

if [[ "$mail_success" != "1" ]]; then
  exit 1
fi
