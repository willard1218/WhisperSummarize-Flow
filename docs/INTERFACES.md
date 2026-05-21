# Extension Interfaces & Contracts

This document defines the expected patterns and data contracts for extending WhisperSummarize-Flow. AI agents should follow these structures to ensure compatibility.

## 1. Transcriber Interface
Any new transcription engine must implement the `BaseTranscriber` interface in `pipeline/transcribers.py`.

- **Method**: `transcribe(self, audio_path: Path, output_dir: Path) -> Path`
- **Requirement**: Must return the path to the generated plain text transcript.
- **Side Effects**: Should ideally generate a `.srt` file in the same directory.

## 2. Notifier Interface
Notification logic in `tools/notifier.py` should follow the provider pattern.

- **Contract**: A Notifier should take content (text/markdown) and a set of recipients.
- **Constraint**: Must handle message splitting/chunking if the platform has character limits (e.g., Telegram's 4096 limit).

## 3. Data Flow Contract
When adding a new source type:
1.  **Registry**: Use `tools/registry.py` to check `is_processed(task_id)`.
2.  **Output Path**: Add the routing logic to `tools/output_paths.py`.
3.  **Metadata**: Always write a `metadata.json` in the task's output directory.

## 5. Telegram Bot Commands
When adding a new command to the listener:
1.  **Handler**: Implement the logic in `_handle_message` within `tools/telegram_listener.py`.
2.  **Registration**: Update the global bot command list using the `setMyCommands` API.
    - **API Endpoint**: `https://api.telegram.org/bot<TOKEN>/setMyCommands`
    - **Payload**: `{"commands": [{"command": "name", "description": "desc"}, ...]}`
## 6. Error Handling & Strict Policy
To ensure system stability and transparency:
1.  **No Silent Fallbacks**: Never implement logic that silently switches to a default configuration or model (e.g., switching AI models or prompt templates) when a preferred choice fails.
2.  **Immediate Notification**: Critical errors in any pipeline stage (Download, Transcribe, Summarize, Notify) must be logged with technical context and immediately sent to the user via Telegram (`send_telegram_msg`).
3.  **Path Resolution**: Always resolve relative paths (like prompt templates) against the `BASE_DIR` to ensure they work correctly under all execution environments (e.g., manual, crontab, launchd).
4.  **Gemini CLI Trust**: When calling the Gemini CLI in automated or non-interactive environments, always include the `--skip-trust` flag or set the `GEMINI_CLI_TRUST_WORKSPACE=true` environment variable to prevent "not running in a trusted directory" errors.
