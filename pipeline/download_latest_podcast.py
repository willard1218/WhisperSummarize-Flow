#!/usr/bin/env python3

import argparse
import json
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
from typing import Optional, List

os.environ.pop("SSLKEYLOGFILE", None)

NO_EPISODE_EXIT_CODE = 2

UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# --- RSS Resolvers (OCP Architecture) ---

class BaseRSSResolver:
    """Base class for platform-specific RSS URL resolvers."""
    def can_handle(self, url: str) -> bool:
        raise NotImplementedError

    def resolve(self, url: str) -> str:
        raise NotImplementedError

class DirectRSSResolver(BaseRSSResolver):
    """Handles URLs that are already direct links to RSS/XML files."""
    def can_handle(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        if parsed.path.lower().endswith((".xml", ".rss")):
            return True
        return any(token in parsed.netloc.lower() for token in ("feeds.", "feed."))

    def resolve(self, url: str) -> str:
        return url

class SoundOnResolver(BaseRSSResolver):
    """Resolves RSS URLs from SoundOn player/show links."""
    def can_handle(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        is_soundon = parsed.netloc.lower() in {"player.soundon.fm", "soundon.fm", "www.soundon.fm"}
        # Ensure it's a channel/show URL, not a specific episode URL (which has /episodes/)
        return is_soundon and "/episodes/" not in parsed.path

    def resolve(self, url: str) -> str:
        uuid_match = UUID_PATTERN.search(url)
        if not uuid_match:
            raise ValueError(f"Could not find SoundOn UUID in URL: {url}")
        return f"https://feeds.soundon.fm/podcasts/{uuid_match.group(0).lower()}.xml"

class ApplePodcastsResolver(BaseRSSResolver):
    """Resolves RSS URLs from Apple Podcasts show pages."""
    def can_handle(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        return parsed.netloc.lower() == "podcasts.apple.com"

    def resolve(self, url: str) -> str:
        data, _ = fetch_bytes(url)
        html = data.decode("utf-8", "ignore")
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

def get_resolvers() -> List[BaseRSSResolver]:
    """Returns all registered resolvers. Order matters."""
    return [cls() for cls in BaseRSSResolver.__subclasses__()]

def resolve_rss_url(url: str) -> str:
    """Dispatches to the appropriate resolver for the given URL."""
    for resolver in get_resolvers():
        if resolver.can_handle(url):
            return resolver.resolve(url)
    raise ValueError(f"Unsupported podcast URL: {url}")

# --- Helper Functions ---

def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "download-latest-podcast/1.0"})
    with urllib.request.urlopen(request) as response:
        return (response.read(), response.info().get_content_type())

def decode_escaped_url(url: str) -> str:
    return (
        url.replace("\\u002F", "/")
        .replace("\\/", "/")
        .replace("\\u0026", "&")
        .replace("&amp;", "&")
    )

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

def parse_episode_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date '{value}'. Expected YYYY-MM-DD.") from exc

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

def download_url_to_file(url: str, destination: Path):
    request = urllib.request.Request(url, headers={"User-Agent": "download-latest-podcast/1.0"})
    with urllib.request.urlopen(request) as response, destination.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk: break
            output.write(chunk)

# --- Direct Episode Handler ---

def handle_direct_episode(url: str, output_dir: Path, show_title_hint: Optional[str] = None) -> int:
    """Special handling for specific episode pages (like SoundOn individual episodes)."""
    parsed = urllib.parse.urlparse(url)
    if "soundon.fm" in parsed.netloc.lower() and "/episodes/" in parsed.path:
        # Extract UUIDs
        uuids = UUID_PATTERN.findall(parsed.path)
        if len(uuids) < 2: return 1
        podcast_uuid, episode_uuid = uuids[0], uuids[1]
        
        # Get RSS to find metadata
        rss_url = f"https://feeds.soundon.fm/podcasts/{podcast_uuid.lower()}.xml"
        rss_data, _ = fetch_bytes(rss_url)
        root = ET.fromstring(rss_data)
        
        channel = root.find("channel")
        feed_title = channel_title(channel) if channel is not None else ""
        
        # Find item by episode_uuid (GUID usually matches)
        item = None
        for candidate in root.findall(".//item"):
            guid = candidate.findtext("guid")
            if guid and episode_uuid in guid:
                item = candidate
                break
        
        if not item: return 1
        
        title = (item.findtext("title") or "episode").strip()
        enclosure = item.find("enclosure")
        if enclosure is None or not enclosure.get("url"): return 1
        
        audio_url = enclosure.get("url", "").strip()
        episode_link = (item.findtext("link") or "").strip()
        filename = sanitize_filename(title)
        show_title = (show_title_hint or feed_title).strip()
        if show_title: filename = f"{sanitize_filename(show_title)} - {filename}"
        
        extension = pick_extension(audio_url, enclosure.get("type"))
        destination = output_dir / f"{filename}{extension}"
        
        if not destination.exists():
            download_url_to_file(audio_url, destination)
            
        print(f"Resolved RSS: {rss_url}")
        print(f"Saved: {destination}")
        print(f"Episode title: {title}")
        if episode_link: print(f"Episode URL: {episode_link}")
        print(f"Audio URL: {audio_url}")
        return 0
    return 1

# --- Main Logic ---

def resolve_apple_episode_guid(episode_id: str) -> Optional[str]:
    """Uses iTunes API to find the RSS GUID for a given Apple Podcast episode ID."""
    url = f"https://itunes.apple.com/lookup?id={episode_id}&entity=podcastEpisode"
    try:
        data, _ = fetch_bytes(url)
        res = json.loads(data.decode("utf-8"))
        for result in res.get("results", []):
            if str(result.get("trackId")) == episode_id:
                return result.get("episodeGuid")
    except Exception as e:
        print(f"Warning: iTunes API lookup failed for {episode_id}: {e}", file=sys.stderr)
    return None

def main() -> int:
    parser = argparse.ArgumentParser(description="Download the latest episode from a podcast RSS feed.")
    parser.add_argument("podcast_url", help="Podcast RSS feed URL, Apple Podcasts show URL, or SoundOn show URL")
    parser.add_argument("-o", "--output-dir", default=".", help="Directory to save the downloaded file")
    parser.add_argument("--prefix-date", action="store_true", help="Prefix the output filename with the pubDate")
    parser.add_argument("--transcribe-script", help="Run this script after download")
    parser.add_argument("--episode-date", type=parse_episode_date, help="Download the episode published on YYYY-MM-DD")
    parser.add_argument("--print-resolved-rss", action="store_true", help="Print the resolved RSS feed URL and exit")
    parser.add_argument("--show-title", help="Prefix the output filename with the podcast title")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Try direct episode handling first (for non-RSS pages like specific SoundOn episode links)
    if not args.print_resolved_rss and not args.episode_date:
        if handle_direct_episode(args.podcast_url, output_dir, args.show_title) == 0:
            # Re-find the file to run transcribe script
            # Note: handle_direct_episode prints its own status
            # For simplicity, we just return 0 here. Improvements could return the path.
            return 0

    try:
        rss_url = resolve_rss_url(args.podcast_url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.print_resolved_rss:
        print(rss_url)
        return 0

    try:
        rss_data, _ = fetch_bytes(rss_url)
        root = ET.fromstring(rss_data)
    except Exception as e:
        print(f"Error fetching/parsing RSS: {e}", file=sys.stderr)
        return 1

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
            print(f"No episode found for {args.episode_date.isoformat()} in feed {rss_url}.", file=sys.stderr)
            return NO_EPISODE_EXIT_CODE
    else:
        # Check for Apple Podcast episode ID (?i=...)
        parsed_url = urllib.parse.urlparse(args.podcast_url)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        apple_id = query_params.get("i", [None])[0]
        if apple_id:
            resolved_guid = resolve_apple_episode_guid(apple_id)
            item = None
            for candidate in channel.findall("item"):
                guid = candidate.findtext("guid")
                link = candidate.findtext("link")
                # Match by resolved GUID first, then fallback to matching the apple_id in guid/link as before
                if (resolved_guid and guid == resolved_guid) or \
                   (guid and apple_id in guid) or \
                   (link and apple_id in link):
                    item = candidate
                    break
            
            if not item:
                print(f"Error: Specific Apple episode {apple_id} (GUID: {resolved_guid}) not found in RSS.", file=sys.stderr)
                return 1
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
    episode_link = (item.findtext("link") or "").strip()
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
        if episode_link: print(f"Episode URL: {episode_link}")
        print(f"Audio URL: {audio_url}")
        if args.transcribe_script:
            subprocess.run([context.args.transcribe_script, str(destination)], check=True)
        return 0

    try:
        download_url_to_file(audio_url, destination)
    except Exception as e:
        print(f"Error downloading audio: {e}", file=sys.stderr)
        if destination.exists(): destination.unlink()
        return 1

    print(f"Resolved RSS: {rss_url}")
    print(f"Saved: {destination}")
    print(f"Episode title: {title}")
    if episode_link: print(f"Episode URL: {episode_link}")
    print(f"Audio URL: {audio_url}")
    if args.transcribe_script:
        subprocess.run([args.transcribe_script, str(destination)], check=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
