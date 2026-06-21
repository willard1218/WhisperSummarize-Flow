#!/usr/bin/env python3

import os
import sys
import subprocess
import shutil
import importlib.util
from pathlib import Path
from typing import List, Tuple

# Setup paths
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.local_config import load_local_config

def check_binary(name: str, env_var: str = None) -> Tuple[bool, str]:
    """Checks if a binary is available in PATH or via environment variable."""
    path = None
    if env_var:
        path = os.environ.get(env_var)
    
    if not path:
        path = shutil.which(name)
        
    if not path:
        return False, f"Binary '{name}' not found in PATH."
    
    # If it's a specific path (either from env or which), verify it's executable or exists
    # shutil.which already returns absolute path or None
    if not os.path.exists(path) and not shutil.which(path):
        return False, f"Binary '{name}' path ('{path}') does not exist or is not executable."
    
    return True, f"Found at: {path}"

def check_python_packages(req_file: Path) -> List[Tuple[bool, str]]:
    """Checks if all packages in requirements.txt are installed."""
    if not req_file.exists():
        return [(False, "requirements.txt not found.")]
    
    results = []
    import importlib.metadata
    
    with open(req_file, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            
            pkg_spec = line.split("==")[0].split(">=")[0].split("<=")[0].strip()
            # Normalize names (e.g. beautifulsoup4 -> bs4 is not direct, but usually works)
            # Special case for some packages
            pkg_map = {
                "beautifulsoup4": "bs4",
                "google-generativeai": "google.generativeai",
                "python-dotenv": "dotenv"
            }
            pkg_name = pkg_map.get(pkg_spec, pkg_spec).replace("-", "_")
            
            try:
                importlib.metadata.version(pkg_spec)
                results.append((True, f"Package '{pkg_spec}' is installed."))
            except importlib.metadata.PackageNotFoundError:
                results.append((False, f"Package '{pkg_spec}' is MISSING."))
                
    return results

def check_file(path: Path, description: str, required: bool = True) -> Tuple[bool, str]:
    """Checks if a file exists and is readable."""
    if path.exists():
        return True, f"{description} found: {path.relative_to(BASE_DIR)}"
    else:
        if required:
            return False, f"{description} is MISSING: {path.relative_to(BASE_DIR) if path.is_relative_to(BASE_DIR) else path}"
        else:
            return True, f"{description} not found (Optional): {path}"

def check_directory(path: Path, description: str) -> Tuple[bool, str]:
    """Checks if a directory exists and is writable."""
    if not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
            return True, f"{description} created: {path.relative_to(BASE_DIR)}"
        except Exception as e:
            return False, f"Failed to create {description}: {e}"
    
    if os.access(path, os.W_OK):
        return True, f"{description} exists and is writable: {path.relative_to(BASE_DIR)}"
    else:
        return False, f"{description} exists but is NOT WRITABLE: {path.relative_to(BASE_DIR)}"

def run_health_check():
    print("=== WhisperSummarize-Flow Health Check ===\n")
    
    # 1. Load Local Config
    load_local_config(BASE_DIR / "config" / "local_config.sh", os.environ)
    
    sections = {
        "Binary Tools": [
            check_binary("ffmpeg", "FFMPEG_BIN"),
            check_binary("ffprobe", "FFPROBE_BIN"),
            check_binary("yt-dlp", "YT_DLP_BIN"),
            check_binary("whisperkit-cli", "WHISPERKIT_BIN"),
            (lambda: (True, "OpenCC found (binary)") if shutil.which("opencc") or os.environ.get("OPENCC_BIN") else (
                (True, "OpenCC found (python fallback)") if importlib.util.find_spec("opencc") else (False, "OpenCC NOT FOUND (binary or python package)")
            ))(),
            (check_binary("opencode") if os.environ.get("ENABLE_OPENCODE", "0") == "1" else (True, "OpenCode check skipped (ENABLE_OPENCODE is not 1)."))
        ],
        "Configuration Files": [
            check_file(BASE_DIR / "config" / "local_config.sh", "Local environment config"),
            check_file(BASE_DIR / "config" / "subscriptions.json", "Podcast subscriptions"),
            check_file(BASE_DIR / "config" / "youtube_subscriptions.json", "YouTube subscriptions"),
            check_file(BASE_DIR / "config" / "recipient_groups.local.json", "Recipient groups", required=False)
        ],
        "Directories & Storage": [
            check_directory(BASE_DIR / "output", "Output directory"),
            check_directory(BASE_DIR / "logs", "Logs directory"),
            check_file(BASE_DIR / "tasks.db", "SQLite Registry Database", required=False)
        ],
        "Environment Variables": [
            (bool(os.environ.get("GEMINI_API_KEY")), "GEMINI_API_KEY is set." if os.environ.get("GEMINI_API_KEY") else "GEMINI_API_KEY is MISSING for Gemini API summarization."),
            (bool(os.environ.get("TELEGRAM_BOT_TOKEN")), "TELEGRAM_BOT_TOKEN is set." if os.environ.get("TELEGRAM_BOT_TOKEN") else "TELEGRAM_BOT_TOKEN is MISSING."),
            (bool(os.environ.get("TELEGRAM_CHAT_ID")), "TELEGRAM_CHAT_ID is set." if os.environ.get("TELEGRAM_CHAT_ID") else "TELEGRAM_CHAT_ID is MISSING.")
        ]
    }
    
    overall_ok = True
    
    for section, results in sections.items():
        print(f"--- {section} ---")
        for ok, msg in results:
            status = "[OK]" if ok else "[FAILED]"
            if not ok: overall_ok = False
            print(f"{status} {msg}")
        print()
        
    print("--- Python Packages ---")
    pkg_results = check_python_packages(BASE_DIR / "requirements.txt")
    missing_pkgs = [msg for ok, msg in pkg_results if not ok]
    if not missing_pkgs:
        print("[OK] All dependencies in requirements.txt are installed.")
    else:
        for msg in missing_pkgs:
            print(f"[FAILED] {msg}")
        overall_ok = False
    print()

    if overall_ok:
        print("Summary: ALL SYSTEMS GO. The environment is healthy.")
    else:
        print("Summary: HEALTH CHECK FAILED. Please fix the issues above before running the pipeline.")
        sys.exit(1)

if __name__ == "__main__":
    run_health_check()
