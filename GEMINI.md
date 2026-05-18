# Project Rules & Conventions

## Project Structure Map
- `pipeline/`: Core execution logic.
  - `run_daily_pipeline.py`: Main entry point and orchestrator.
  - `transcribers.py`: Audio-to-text engine wrappers (WhisperKit/Whisper.cpp).
  - `run_registered_*.py`: Source-specific sync logic.
- `tools/`: Utility modules.
  - `notifier.py`: Telegram and Email notification delivery.
  - `registry.py`: SQLite-based state management for processed tasks.
  - `config_models.py`: Pydantic definitions for configuration.
  - `logger.py`: Structured logging utility.
  - `retry.py`: Exponential backoff decorator.
- `prompts/`: System prompts for Gemini AI summarization.
- `config/`: JSON/Shell configuration files (ignored by git).
- `output/`: Processed transcripts, audio, and summaries (ignored by git).

## Telegram Bot Rules
- **Command Registration**: Whenever a new command is added to `tools/telegram_listener.py`, it **must** be registered via the Telegram Bot API (`setMyCommands`) so that it appears in the autocomplete menu.
- **Emoji Handling**: All bot responses should follow the "No Emojis" rule for system labels, but may use them sparingly in user-facing status reports if requested.

## Code & Logging
- **Structured Logging**: Use the `tools/logger.py` utility. Prefer Key-Value (KV) format for logs. Use keywords like `task`, `action`, `status`, and `duration` to provide context for AI agents.
- **No Emojis**: Do not use emojis in code, log messages, or any system-generated output. Use plain text labels (e.g., [OK], [FAILED], [SKIPPED]) instead.
- **Language**: Core logic and comments should be in English or Traditional Chinese as established in the project.
- **Privacy**: Never log or commit private tokens, API keys, or personal information.

## Testing & Debugging
- **No Ad-hoc Scripts**: Never create separate temporary Python scripts (e.g., `debug_*.py`, `manual_test.py`) for debugging or verification.
- **Document Procedures**: Record any manual verification commands or reproduction steps directly in `docs/TESTING.md` under a relevant task-specific section or as reusable snippets.
- **Use Existing Tools**: Prioritize using `run_daily_pipeline.py --debug`, `tools/check_daily_status.py`, or existing unit tests for verification.
