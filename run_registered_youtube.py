#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from recipient_groups import resolve_emails, load_recipient_groups


def load_subscriptions(path: Path) -> list[dict]:
    if not path.exists():
        return []

    data = json.loads(path.read_text(encoding="utf-8"))
    subscriptions = data.get("subscriptions", [])
    if not isinstance(subscriptions, list):
        raise SystemExit(f"Invalid subscriptions list in {path}")
    return subscriptions


def make_channel_slug(channel_url: str) -> str:
    tail = channel_url.rstrip("/").rsplit("/", 2)[0].rsplit("/", 1)[-1]
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", tail).strip("-").lower()
    return cleaned or "youtube-channel"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Process all registered YouTube livestream channel subscriptions."
    )
    parser.add_argument(
        "--config",
        default="youtube_subscriptions.json",
        help="Path to the YouTube subscriptions JSON file (default: youtube_subscriptions.json)",
    )
    parser.add_argument(
        "--output-root",
        default="output",
        help="Directory for downloaded audio and transcripts (default: output)",
    )
    parser.add_argument(
        "--transcribe-script",
        default=os.environ.get("GENSRT_SCRIPT", "gensrt.sh"),
        help="Transcription script to run after download",
    )
    parser.add_argument(
        "--recipient-config",
        default=os.environ.get("RECIPIENT_CONFIG_FILE", "recipient_groups.local.json"),
        help="Path to local recipient groups JSON (default: recipient_groups.local.json)",
    )
    parser.add_argument(
        "--traditionalize-transcript",
        action="store_true",
        default=os.environ.get("OPENCC_TRADITIONALIZE", "0") == "1",
        help="Convert Simplified Chinese transcripts to Traditional Chinese through OpenCC.",
    )
    parser.add_argument(
        "--opencc-config",
        default=os.environ.get("OPENCC_CONFIG", "s2twp.json"),
        help="OpenCC config name or path (default: s2twp.json)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug mode: only send emails to the address specified in DEBUG_RECIPIENT env var",
    )
    args = parser.parse_args()

    debug_email = os.environ.get("DEBUG_RECIPIENT")
    if args.debug and not debug_email:
        print("DEBUG_RECIPIENT environment variable is not set.", file=sys.stderr)
        return 1

    base_dir = Path(__file__).resolve().parent
    config_path = Path(args.config).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    recipient_config = Path(args.recipient_config).expanduser().resolve()
    runner = base_dir / "download_and_transcribe_latest.sh"
    subscriptions = load_subscriptions(config_path)
    recipient_groups = load_recipient_groups(recipient_config)

    if not subscriptions:
        print(f"No YouTube subscriptions found in {config_path}")
        return 0

    output_root.mkdir(parents=True, exist_ok=True)
    overall_success = True

    for subscription in subscriptions:
        channel_url = (subscription.get("channel_url") or "").strip()
        emails = resolve_emails(subscription, recipient_groups)
        if args.debug and debug_email:
            emails = [debug_email]

        if not channel_url or not emails:
            print(f"Skipping invalid YouTube subscription: {subscription}")
            overall_success = False
            continue

        channel_output_dir = output_root / make_channel_slug(channel_url)
        channel_output_dir.mkdir(parents=True, exist_ok=True)

        print(f"== Processing {channel_url}")
        env = os.environ.copy()
        env["OPENCC_TRADITIONALIZE"] = "1" if args.traditionalize_transcript else "0"
        env["OPENCC_CONFIG"] = args.opencc_config

        result = subprocess.run(
            [
                "/bin/bash",
                str(runner),
                channel_url,
                str(channel_output_dir),
                ",".join(emails),
                args.transcribe_script,
            ],
            text=True,
            capture_output=True,
            env=env,
        )

        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.returncode != 0:
            if result.stderr:
                print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
            overall_success = False

    return 0 if overall_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
