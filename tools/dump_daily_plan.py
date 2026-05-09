#!/usr/bin/env python3

import argparse
import json
from pathlib import Path
from typing import Any

from recipient_groups import load_recipient_groups, resolve_emails


def load_subscriptions(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    data = json.loads(path.read_text(encoding="utf-8"))
    subscriptions = data.get("subscriptions", [])
    if not isinstance(subscriptions, list):
        raise SystemExit(f"Invalid subscriptions list in {path}")
    return [item for item in subscriptions if isinstance(item, dict)]


def recipient_group_names(subscription: dict[str, Any]) -> list[str]:
    names: list[str] = []
    group_name = subscription.get("recipient_group")
    if isinstance(group_name, str) and group_name.strip():
        names.append(group_name.strip())

    extra_names = subscription.get("recipient_groups", [])
    if isinstance(extra_names, list):
        names.extend(
            name.strip()
            for name in extra_names
            if isinstance(name, str) and name.strip()
        )

    return sorted(set(names))


def build_daily_plan(
    podcast_config: Path,
    youtube_config: Path,
    recipient_config: Path,
) -> list[dict[str, Any]]:
    groups = load_recipient_groups(recipient_config)
    plan: list[dict[str, Any]] = []

    for index, subscription in enumerate(load_subscriptions(podcast_config), start=1):
        emails = resolve_emails(subscription, groups)
        plan.append(
            {
                "label": f"Podcast {index}",
                "type": "podcast",
                "order": len(plan) + 1,
                "podcast_url": subscription.get("podcast_url") or "",
                "rss_url": subscription.get("rss_url") or "",
                "podcast_title": subscription.get("podcast_title") or "",
                "prompt_file": subscription.get("prompt_file") or "prompts/default.md",
                "recipient_groups": recipient_group_names(subscription),
                "emails": emails,
            }
        )

    for index, subscription in enumerate(load_subscriptions(youtube_config), start=1):
        emails = resolve_emails(subscription, groups)
        plan.append(
            {
                "label": f"YouTube {index}" if index > 1 else "YouTube",
                "type": "youtube",
                "order": len(plan) + 1,
                "channel_url": subscription.get("channel_url") or "",
                "prompt_file": subscription.get("prompt_file") or "prompts/default.md",
                "recipient_groups": recipient_group_names(subscription),
                "emails": emails,
            }
        )

    return plan


def print_text(plan: list[dict[str, Any]], show_urls: bool, show_groups: bool, show_prompts: bool) -> None:
    for item in plan:
        emails = item["emails"]
        recipients = ", ".join(emails) if emails else "(no recipients)"
        print(f"{item['label']}: {recipients}")

        if show_urls:
            if item["type"] == "podcast":
                if item.get("podcast_url"):
                    print(f"  podcast_url: {item['podcast_url']}")
                if item.get("rss_url"):
                    print(f"  rss_url: {item['rss_url']}")
            elif item.get("channel_url"):
                print(f"  channel_url: {item['channel_url']}")

        if show_groups and item["recipient_groups"]:
            print(f"  groups: {', '.join(item['recipient_groups'])}")
            
        if show_prompts and item.get("prompt_file"):
            print(f"  prompt_file: {item['prompt_file']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dump the daily crawl order and resolved mail recipients."
    )
    parser.add_argument(
        "--podcast-config",
        default="config/subscriptions.json",
        help="Path to podcast subscriptions JSON (default: config/subscriptions.json)",
    )
    parser.add_argument(
        "--youtube-config",
        default="config/youtube_subscriptions.json",
        help="Path to YouTube subscriptions JSON (default: config/youtube_subscriptions.json)",
    )
    parser.add_argument(
        "--recipient-config",
        default="config/recipient_groups.local.json",
        help="Path to local recipient groups JSON (default: config/recipient_groups.local.json)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--show-urls",
        action="store_true",
        help="Include source URLs under each item in text output",
    )
    parser.add_argument(
        "--show-groups",
        action="store_true",
        help="Include recipient group names under each item in text output",
    )
    parser.add_argument(
        "--show-prompts",
        action="store_true",
        help="Include the configured AI prompt file under each item in text output",
    )
    args = parser.parse_args()

    plan = build_daily_plan(
        Path(args.podcast_config).expanduser().resolve(),
        Path(args.youtube_config).expanduser().resolve(),
        Path(args.recipient_config).expanduser().resolve(),
    )

    if args.format == "json":
        print(json.dumps({"daily_plan": plan}, ensure_ascii=False, indent=2))
    else:
        print_text(plan, show_urls=args.show_urls, show_groups=args.show_groups, show_prompts=args.show_prompts)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

if __name__ == "__main__":
    raise SystemExit(main())
