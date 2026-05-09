#!/bin/bash
set -euo pipefail

# --- Configuration (Override via environment variables) ---
FFMPEG_BIN="${FFMPEG_BIN:-ffmpeg}"
WHISPER_BIN="${WHISPER_BIN:-main}"
WHISPER_MODEL="${WHISPER_MODEL:-models/ggml-large-v3.bin}"

# --- Lock Mechanism ---
LOCK_DIR="${TMPDIR:-/tmp}/gensrt.lock"
release_lock() {
  if [[ -d "$LOCK_DIR" ]] && [[ "$(cat "$LOCK_DIR/pid" 2>/dev/null)" == "$$" ]]; then
    rm -rf "$LOCK_DIR"
  fi
}
acquire_lock() {
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    if [[ -f "$LOCK_DIR/pid" ]]; then
      lock_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
      if [[ -n "${lock_pid:-}" ]] && ! kill -0 "$lock_pid" 2>/dev/null; then
        rm -rf "$LOCK_DIR"
        continue
      fi
    fi
    sleep 5
  done
  echo "$$" > "$LOCK_DIR/pid"
  trap release_lock EXIT INT TERM
}

# --- Core Logic ---
run_whisper() {
  local mc_flag="${1:-}"
  local -a args=(-m "$WHISPER_MODEL" -l "zh" -f "$wav_path" -otxt -osrt -of "${audio_path%.*}")
  [[ "$mc_flag" == "no-context" ]] && args+=(-mc 0)
  "$WHISPER_BIN" "${args[@]}"
  [[ -f "${audio_path%.*}.srt" ]] && mv "${audio_path%.*}.srt" "${audio_path%.*}.srt.txt"
}

check_repetition() {
  /usr/bin/python3 - "${audio_path%.*}.srt.txt" <<'PY'
import sys, re
from collections import Counter
lines = [re.sub(r"\s+", "", l.strip()) for l in open(sys.argv[1]).splitlines() if l.strip() and not l.strip().isdigit() and "-->" not in l]
if len(lines) < 4: sys.exit(0)
counts = Counter(lines)
_, count = counts.most_common(1)[0]
if count >= 4 and (count / len(lines)) >= 0.5: sys.exit(2)
PY
}

# --- Main ---
[[ $# -ne 1 ]] && { echo "Usage: $0 AUDIO_FILE"; exit 1; }
audio_path="$1"
wav_path="${audio_path%.*}.wav"

acquire_lock

# Convert to WAV
"$FFMPEG_BIN" -y -i "$audio_path" -ac 1 -ar 16000 "$wav_path"

# First try: default
run_whisper
if ! check_repetition; then
  echo "Repetition detected, retrying without context..."
  run_whisper "no-context"
fi

rm -f "$wav_path"
