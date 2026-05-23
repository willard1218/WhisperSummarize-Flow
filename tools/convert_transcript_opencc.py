#!/usr/bin/env python3

import argparse
import subprocess
import os
import sys
import shutil
from pathlib import Path

# Setup paths to import logger
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "tools"))
from logger import setup_logging, get_logger

logger = get_logger("opencc")

def resolve_opencc_bin() -> str:
    """Resolve the opencc binary path with fallbacks."""
    # 1. Check environment variable
    env_bin = os.environ.get("OPENCC_BIN")
    if env_bin:
        return env_bin
        
    # 2. Check PATH
    path_bin = shutil.which("opencc")
    if path_bin:
        return path_bin
        
    # 3. Check common locations on macOS/Linux
    fallbacks = [
        "/opt/homebrew/bin/opencc",
        "/usr/local/bin/opencc",
        "/usr/bin/opencc"
    ]
    for p in fallbacks:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
            
    # 4. Final fallback to just the name
    return "opencc"

def translated_path_for(path: Path) -> Path:
    if path.name.endswith(".srt.txt"):
        return path.with_name(path.name[:-8] + ".zh-Hant.srt.txt")
    if path.name.endswith(".txt"):
        return path.with_name(path.name[:-4] + ".zh-Hant.txt")
    raise ValueError("Unsupported extension")

def main() -> int:
    parser = argparse.ArgumentParser(description="Convert transcript to Traditional Chinese")
    parser.add_argument("input_path", help="Path to transcript")
    parser.add_argument("--output-path", help="Path to output")
    parser.add_argument("--config", default="s2twp.json", help="OpenCC config")
    args = parser.parse_args()

    setup_logging(format_type="kv")

    input_path = Path(args.input_path).expanduser().resolve()
    if not input_path.exists():
        logger.error(f"Input not found path={input_path}", action="convert_fail")
        return 1

    output_path = Path(args.output_path).expanduser().resolve() if args.output_path else translated_path_for(input_path)

    logger.info(f"Converting file path={input_path.name}", action="convert_start")
    
    opencc_bin = resolve_opencc_bin()
    try:
        res = subprocess.run([opencc_bin, "-i", str(input_path), "-o", str(output_path), "-c", args.config], capture_output=True)
        if res.returncode == 0:
            logger.info(f"Conversion ok path={output_path.name}", action="convert_ok")
            print(output_path)
            return 0
        else:
            logger.error(f"OpenCC binary failed status={res.returncode} stderr={res.stderr.decode()}", action="convert_error")
            # Fallback to python implementation if binary fails or is not found
            raise FileNotFoundError
    except FileNotFoundError:
        logger.info("OpenCC binary not found or failed, falling back to python implementation", action="convert_fallback")
        try:
            from opencc import OpenCC
            # OpenCC(args.config) works if the config name matches what the library supports
            # The library supports configs like 's2t', 't2s', 's2tw', 'tw2s', 's2hk', 'hk2s', 's2twp', 'tw2sp', 't2tw', 't2hk'
            # Remove .json suffix if present
            config_name = args.config
            if config_name.endswith(".json"):
                config_name = config_name[:-5]
            
            converter = OpenCC(config_name)
            content = input_path.read_text(encoding="utf-8")
            converted = converter.convert(content)
            output_path.write_text(converted, encoding="utf-8")
            
            logger.info(f"Conversion ok (python) path={output_path.name}", action="convert_ok")
            print(output_path)
            return 0
        except Exception as e:
            logger.error(f"OpenCC python fallback failed: {e}", action="convert_fail")
            raise RuntimeError(f"OpenCC conversion failed: {e}")

if __name__ == "__main__":
    raise SystemExit(main())
