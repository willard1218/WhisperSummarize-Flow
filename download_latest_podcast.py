#!/usr/bin/env python3

import argparse
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

os.environ.pop("SSLKEYLOGFILE", None)

NO_EPISODE_EXIT_CODE = 2


UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "download-latest-podcast/1.0",
        },
    )
    with urllib.request.urlopen(request) as response:
        return response.read()


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return cleaned or "latest_podcast"


def pick_extension(audio_url: str, content_type: Optional[str]) -> str:
    path = urllib.parse.urlparse(audio_url).path
    suffix = Path(path).suffix.lower()
    if suffix:
        return suffix
    if content_type == "audio/mpeg":
        return ".mp3"
    return ".bin"


def decode_escaped_url(url: str) -> str:
    return (
        url.replace("\\u002F", "/")
        .replace("\\/", "/")
        .replace("\\u0026", "&")
        .replace("&amp;", "&")
    )


def looks_like_rss_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.path.lower().endswith((".xml", ".rss")):
        return True
    return any(token in parsed.netloc.lower() for token in ("feeds.", "feed."))


def resolve_soundon_rss_url(url: str) -> Optional[str]:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if host not in {"player.soundon.fm", "soundon.fm", "www.soundon.fm"}:
        return None

    uuid_match = UUID_PATTERN.search(parsed.path)
    if not uuid_match:
        return None
    return f"https://feeds.soundon.fm/podcasts/{uuid_match.group(0).lower()}.xml"


def resolve_apple_rss_url(url: str) -> str:
    html = fetch_bytes(url).decode("utf-8", "ignore")
    patterns = [
        r'"feedUrl":"([^"]+)"',
        r'"feedUrl":"(https:[^"]+)"',
        r'"feedUrl":\s*"([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return decode_escaped_url(match.group(1))
    raise ValueError("Could not find feedUrl on the Apple Podcasts page.")


def resolve_rss_url(url: str) -> str:
    if looks_like_rss_url(url):
        return url

    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()

    soundon_rss = resolve_soundon_rss_url(url)
    if soundon_rss:
        return soundon_rss

    if host == "podcasts.apple.com":
        return resolve_apple_rss_url(url)

    raise ValueError(f"Unsupported podcast URL: {url}")


def parse_episode_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date '{value}'. Expected YYYY-MM-DD."
        ) from exc


def channel_title(channel: ET.Element) -> str:
    return (channel.findtext("title") or "").strip()


def item_pub_date(item: ET.Element) -> Optional[date]:
    pub_date = (item.findtext("pubDate") or "").strip()
    if not pub_date:
        return None

    try:
        parsed = parsedate_to_datetime(pub_date)
    except (TypeError, ValueError, IndexError):
        return None

    if parsed.tzinfo is not None:
        return parsed.astimezone().date()
    return parsed.date()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download the latest episode from a podcast RSS feed or a podcast channel page."
    )
    parser.add_argument(
        "podcast_url",
        help="Podcast RSS feed URL, Apple Podcasts show URL, or SoundOn show URL",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=".",
        help="Directory to save the downloaded file into (default: current directory)",
    )
    parser.add_argument(
        "--prefix-date",
        action="store_true",
        help="Prefix the output filename with the episode pubDate in YYYY-MM-DD if available",
    )
    parser.add_argument(
        "--transcribe-script",
        help="Run this script with the downloaded audio file path after download completes",
    )
    parser.add_argument(
        "--episode-date",
        type=parse_episode_date,
        help="Download the episode published on YYYY-MM-DD in local time",
    )
    parser.add_argument(
        "--print-resolved-rss",
        action="store_true",
        help="Print the resolved RSS feed URL and exit",
    )
    parser.add_argument(
        "--show-title",
        help="Prefix the output filename with the podcast/show title",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        rss_url = resolve_rss_url(args.podcast_url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.print_resolved_rss:
        print(rss_url)
        return 0

    rss_data = fetch_bytes(rss_url)
    root = ET.fromstring(rss_data)

    channel = root.find("channel")
    if channel is None:
        print("RSS feed is missing a <channel> element.", file=sys.stderr)
        return 1

    feed_title = channel_title(channel)

    if args.episode_date:
        item = None
        for candidate in channel.findall("item"):
            if item_pub_date(candidate) == args.episode_date:
                item = candidate
                break
        if item is None:
            print(
                f"No episode found for {args.episode_date.isoformat()} in feed {rss_url}.",
                file=sys.stderr,
            )
            return NO_EPISODE_EXIT_CODE
    else:
        item = channel.find("item")

    if item is None:
        print("RSS feed does not contain any episodes.", file=sys.stderr)
        return 1

    title = (item.findtext("title") or "latest_podcast").strip()
    pub_date = (item.findtext("pubDate") or "").strip()
    enclosure = item.find("enclosure")
    if enclosure is None or not enclosure.get("url"):
        print("Latest episode does not have an enclosure URL.", file=sys.stderr)
        return 1

    audio_url = enclosure.get("url", "").strip()
    filename = sanitize_filename(title)
    show_title = (args.show_title or feed_title).strip()
    if show_title:
        filename = f"{sanitize_filename(show_title)} - {filename}"
    if args.prefix_date and pub_date:
        filename = f"{sanitize_filename(pub_date[:16])} {filename}"

    extension = pick_extension(audio_url, enclosure.get("type"))
    destination = output_dir / f"{filename}{extension}"

    if destination.exists():
        print(f"Resolved RSS: {rss_url}")
        print(f"File already exists: {destination}")
        print(f"Episode title: {title}")
        print(f"Audio URL: {audio_url}")
        if args.transcribe_script:
            subprocess.run([args.transcribe_script, str(destination)], check=True)
        return 0

    request = urllib.request.Request(
        audio_url,
        headers={"User-Agent": "download-latest-podcast/1.0"},
    )
    with urllib.request.urlopen(request) as response, destination.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)

    print(f"Resolved RSS: {rss_url}")
    print(f"Saved: {destination}")
    print(f"Episode title: {title}")
    print(f"Audio URL: {audio_url}")
    if args.transcribe_script:
        subprocess.run([args.transcribe_script, str(destination)], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
