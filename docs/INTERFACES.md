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
3.  **Validation**: Ensure the command works correctly and appears in the Telegram autocomplete menu after an app restart.
