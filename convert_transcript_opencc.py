#!/usr/bin/env python3

import argparse
import subprocess
from pathlib import Path


def translated_path_for(path: Path) -> Path:
    if path.name.endswith(".srt.txt"):
        return path.with_name(path.name[:-8] + ".zh-Hant.srt.txt")
    raise ValueError("Only .srt.txt transcripts are supported for OpenCC conversion.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a transcript from Simplified Chinese to Traditional Chinese using OpenCC."
    )
    parser.add_argument("input_path", help="Path to the original transcript")
    parser.add_argument(
        "--output-path",
        help="Path to write the converted transcript (default: alongside original with .zh-Hant suffix)",
    )
    parser.add_argument(
        "--config",
        default="s2twp.json",
        help="OpenCC config name or path (default: s2twp.json)",
    )
    args = parser.parse_args()

    input_path = Path(args.input_path).expanduser().resolve()
    if not input_path.name.endswith(".srt.txt"):
        raise SystemExit("Only .srt.txt transcripts are supported for OpenCC conversion.")
    if not input_path.exists():
        raise SystemExit(f"Transcript not found: {input_path}")

    output_path = (
        Path(args.output_path).expanduser().resolve()
        if args.output_path
        else translated_path_for(input_path)
    )

    result = subprocess.run(
        [
            "opencc",
            "-i",
            str(input_path),
            "-o",
            str(output_path),
            "-c",
            args.config,
        ]
    )
    if result.returncode != 0:
        return result.returncode

    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
