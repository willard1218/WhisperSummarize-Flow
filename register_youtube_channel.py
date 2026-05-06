#!/usr/bin/env python3

import argparse
import json
import re
import urllib.parse
from pathlib import Path


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_email(value: str) -> str:
    email = value.strip().lower()
    if not EMAIL_PATTERN.fullmatch(email):
        raise argparse.ArgumentTypeError(f"Invalid email address: {value}")
    return email


def normalize_channel_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise argparse.ArgumentTypeError(f"Invalid YouTube URL: {value}")
    if parsed.netloc.lower() not in {"youtube.com", "www.youtube.com"}:
        raise argparse.ArgumentTypeError(f"Unsupported YouTube host: {value}")

    path = parsed.path.rstrip("/")
    if not path:
        raise argparse.ArgumentTypeError(f"Invalid YouTube channel path: {value}")
    if not path.endswith("/streams"):
        path = f"{path}/streams"

    normalized = urllib.parse.urlunparse(("https", "www.youtube.com", path, "", "", ""))
    return normalized


def load_config(path: Path) -> dict:
    if not path.exists():
        return {"subscriptions": []}

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Invalid config format in {path}")
    subscriptions = data.get("subscriptions")
    if not isinstance(subscriptions, list):
        raise SystemExit(f"Invalid subscriptions list in {path}")
    return data


def save_config(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register a YouTube livestream channel URL with recipient emails or a local recipient group."
    )
    parser.add_argument("channel_url", type=normalize_channel_url, help="YouTube channel or /streams URL")
    parser.add_argument("emails", nargs="*", type=validate_email, help="Recipient emails")
    parser.add_argument(
        "--recipient-group",
        help="Local recipient group name stored in recipient_groups.local.json",
    )
    parser.add_argument(
        "--config",
        default="youtube_subscriptions.json",
        help="Path to the YouTube subscriptions JSON file (default: youtube_subscriptions.json)",
    )
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    data = load_config(config_path)
    subscriptions = data["subscriptions"]

    if not args.emails and not args.recipient_group:
        raise SystemExit("Provide recipient emails or --recipient-group.")

    subscription = next(
        (item for item in subscriptions if item.get("channel_url") == args.channel_url),
        None,
    )

    if subscription is None:
        subscription = {
            "channel_url": args.channel_url,
            "emails": [],
        }
        subscriptions.append(subscription)

    merged_emails = sorted(set(subscription.get("emails", [])) | set(args.emails))
    subscription["emails"] = merged_emails
    if args.recipient_group:
        subscription["recipient_group"] = args.recipient_group.strip()
    subscriptions.sort(key=lambda item: item.get("channel_url", ""))

    save_config(config_path, data)

    print(f"Config: {config_path}")
    print(f"YouTube channel: {subscription['channel_url']}")
    if args.recipient_group:
        print(f"Recipient group: {args.recipient_group.strip()}")
    if merged_emails:
        print("Registered emails:")
        for email in merged_emails:
            print(f"- {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
