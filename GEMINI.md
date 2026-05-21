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
- **Environment Check**: ALWAYS run `venv/bin/python3 tools/health_check.py` before starting work to ensure all dependencies and configurations are correct.
- **No Ad-hoc Scripts**: Never create separate temporary Python scripts (e.g., `debug_*.py`, `manual_test.py`) for debugging or verification.
- **Document Procedures**: Record any manual verification commands or reproduction steps directly in `docs/TESTING.md` under a relevant task-specific section or as reusable snippets.
- **Use Existing Tools**: Prioritize using `run_daily_pipeline.py --debug`, `tools/check_daily_status.py`, or existing unit tests for verification.

## Useful Snippets for AI Agents
- **Re-run Today's Pipeline**: `venv/bin/python3 pipeline/run_daily_pipeline.py --date $(date +%Y-%m-%d)`
- **Check Progress**: `venv/bin/python3 tools/check_daily_status.py`
- **Force Re-summarize**: `rm output/path/to/*.summary.md && venv/bin/python3 pipeline/run_daily_pipeline.py --url [URL]`

## Autonomous Self-Healing (Auto-Fixer)
The project includes a self-healing mechanism to autonomously resolve pipeline failures:
- **Trigger**: When a task fails in `run_daily_pipeline.py`, it automatically launches `tools/auto_fixer.py` in the background.
- **Workflow**:
    1. Gathers context: `README.md`, `GEMINI.md`, and relevant task logs.
    2. Invokes Gemini AI: Runs `gemini --yolo --skip-trust` with a high-level repair prompt.
    3. AI performs repairs: Modifies code, configs, or environments directly.
    4. Reports result: Sends a Telegram message with [Root Cause], [Solution], and [Status].
- **AI Agent Responsibility**: When acting as the auto-fixer, prioritize stability and follow all rules in `GEMINI.md`. Always provide a clear report upon completion.
