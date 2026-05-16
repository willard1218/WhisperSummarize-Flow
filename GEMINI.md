# Project Rules & Conventions

## Code & Logging
- **No Emojis**: Do not use emojis in code, log messages, or any system-generated output. Use plain text labels (e.g., [OK], [FAILED], [SKIPPED]) instead.
- **Language**: Core logic and comments should be in English or Traditional Chinese as established in the project.
- **Privacy**: Never log or commit private tokens, API keys, or personal information.

## Testing & Debugging
- **No Ad-hoc Scripts**: Never create separate temporary Python scripts (e.g., `debug_*.py`, `manual_test.py`) for debugging or verification.
- **Document Procedures**: Record any manual verification commands or reproduction steps directly in `docs/TESTING.md` under a relevant task-specific section or as reusable snippets.
- **Use Existing Tools**: Prioritize using `run_daily_pipeline.py --debug`, `tools/check_daily_status.py`, or existing unit tests for verification.
