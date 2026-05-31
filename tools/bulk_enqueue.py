#!/usr/bin/env python3

import os
import sys
import time
import argparse
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from project_runtime import bootstrap_project, load_project_env
BASE_DIR = bootstrap_project(ROOT_DIR)
load_project_env(BASE_DIR)

from tools.telegram_listener import (
    TelegramUpdateHandler, 
    ListenerSettings, 
    TelegramApiClient, 
    TranscriptionStatusProvider, 
    PipelineLauncher, 
    TelegramFileDownloader,
    URL_PATTERN,
    SUPPORTED_URL_HOSTS
)
from tools.registry import Registry

def main():
    parser = argparse.ArgumentParser(description="Bulk enqueue YouTube/Podcast URLs from a text file into the task queue.")
    parser.add_argument("input_file", help="Path to the text file containing URLs")
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"Error: File {args.input_file} not found.")
        sys.exit(1)

    # Load settings
    registry = Registry(BASE_DIR / "tasks.db")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    owner_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not token or not owner_id:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in environment.")
        sys.exit(1)

    settings = ListenerSettings(BASE_DIR, token, owner_id, sys.executable)
    api_client = TelegramApiClient(settings)
    status_provider = TranscriptionStatusProvider()
    pipeline_launcher = PipelineLauncher(BASE_DIR, sys.executable)
    
    handler = TelegramUpdateHandler(
        settings, api_client, status_provider, 
        pipeline_launcher, TelegramFileDownloader(api_client, settings.bot_token),
        registry
    )

    # Extract URLs from file
    content = input_path.read_text(encoding="utf-8")
    raw_urls = URL_PATTERN.findall(content)
    urls = [url for url in raw_urls if any(host in url for host in SUPPORTED_URL_HOSTS)]

    if not urls:
        print("No supported URLs found in the file.")
        return

    print(f"Found {len(urls)} supported URLs. Injecting into queue...")
    
    for i, url in enumerate(urls):
        print(f"[{i+1}/{len(urls)}] Processing: {url}")
        # Simulate a message so the user gets notified and queue logic triggers
        update = {
            "update_id": int(time.time()) + i,
            "message": {
                "message_id": 10000 + i,
                "chat": {"id": owner_id},
                "text": url
            }
        }
        handler.handle(update)
        time.sleep(0.5) # Prevent Telegram API rate limiting

    print("\nBulk injection complete. System is now processing the queue.")

if __name__ == "__main__":
    main()
