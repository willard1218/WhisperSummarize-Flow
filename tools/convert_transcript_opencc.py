#!/usr/bin/env python3

import argparse
import subprocess
import sys
from pathlib import Path

# Setup paths to import logger
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "tools"))
from logger import setup_logging, get_logger

logger = get_logger("opencc")

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
    res = subprocess.run(["opencc", "-i", str(input_path), "-o", str(output_path), "-c", args.config])
    
    if res.returncode == 0:
        logger.info(f"Conversion ok path={output_path.name}", action="convert_ok")
        print(output_path)
        return 0
    
    logger.error(f"OpenCC failed status={res.returncode}", action="convert_error")
    return res.returncode

if __name__ == "__main__":
    raise SystemExit(main())
