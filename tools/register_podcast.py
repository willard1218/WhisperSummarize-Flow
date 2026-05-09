#!/usr/bin/env python3

import argparse
import json
import re
import xml.etree.ElementTree as ET
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from download_latest_podcast import fetch_bytes, resolve_rss_url


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_email(value: str) -> str:
    email = value.strip().lower()
    if not EMAIL_PATTERN.fullmatch(email):
        raise argparse.ArgumentTypeError(f"Invalid email address: {value}")
    return email


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


def resolve_podcast_title(rss_url: str) -> str:
    rss_data = fetch_bytes(rss_url)
    root = ET.fromstring(rss_data)
    channel = root.find("channel")
    if channel is None:
        return ""
    return (channel.findtext("title") or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register a podcast URL with recipient emails or a local recipient group."
    )
    parser.add_argument("podcast_url", help="Apple Podcasts, SoundOn, or RSS URL")
    parser.add_argument("emails", nargs="*", type=validate_email, help="Recipient emails")
    parser.add_argument(
        "--recipient-group",
        help="Local recipient group name stored in recipient_groups.local.json",
    )
    parser.add_argument(
        "--config",
        default="subscriptions.json",
        help="Path to the subscriptions JSON file (default: subscriptions.json)",
    )
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    data = load_config(config_path)
    rss_url = resolve_rss_url(args.podcast_url)
    podcast_title = resolve_podcast_title(rss_url)

    if not args.emails and not args.recipient_group:
        raise SystemExit("Provide recipient emails or --recipient-group.")

    subscriptions = data["subscriptions"]
    subscription = next(
        (item for item in subscriptions if item.get("rss_url") == rss_url),
        None,
    )

    if subscription is None:
        subscription = {
            "podcast_url": args.podcast_url,
            "rss_url": rss_url,
            "podcast_title": podcast_title,
            "emails": [],
        }
        subscriptions.append(subscription)
    else:
        subscription["podcast_url"] = args.podcast_url
        subscription["podcast_title"] = podcast_title

    merged_emails = sorted(set(subscription.get("emails", [])) | set(args.emails))
    subscription["emails"] = merged_emails
    if args.recipient_group:
        subscription["recipient_group"] = args.recipient_group.strip()
    subscriptions.sort(key=lambda item: item.get("rss_url", ""))

    save_config(config_path, data)

    print(f"Config: {config_path}")
    print(f"Podcast URL: {subscription['podcast_url']}")
    print(f"Resolved RSS: {rss_url}")
    if podcast_title:
        print(f"Podcast title: {podcast_title}")
    if args.recipient_group:
        print(f"Recipient group: {args.recipient_group.strip()}")
    if merged_emails:
        print("Registered emails:")
        for email in merged_emails:
            print(f"- {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
