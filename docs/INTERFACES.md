# Extension Interfaces & Contracts

This document defines the interfaces that are actually used by the current codebase. If a document here conflicts with runtime behavior, prefer the code.

## 1. Transcriber Interface
Any new transcription engine must implement the `BaseTranscriber` interface in `pipeline/transcribers.py`.

- **Method**: `transcribe(self, audio_path: Path, output_dir: Path) -> Path`
- **Requirement**: Must return the generated `.srt.txt` path or `None`.
- **Side Effects**: The current pipeline also expects the matching plain text transcript (`.txt`) to exist for summarization.

## 2. Notifier Interface
Notification logic in `tools/notifier.py` should follow the provider pattern.

- **Contract**: A notifier class must extend `BaseNotifier`, implement `is_enabled(args)` and `notify(items, args)`, and tolerate per-item failures.
- **Constraint**: Telegram-capable notifiers must handle message chunking.

## 3. Data Flow Contract
When adding a new source type:
1. **Output Path**: Add routing logic to `tools/output_paths.py`.
2. **Task Build**: Update `build_items(...)` in `pipeline/run_daily_pipeline.py` or the relevant listener/source adapter.
3. **Metadata**: Always write a `metadata.json` in the task output directory.
4. **Tests**: Add or update unit tests for path routing and task construction.

## 4. Runtime Bootstrap Contract

Core entrypoints should use the shared runtime helpers:

1. Add the project root to `sys.path`.
2. Call `project_runtime.bootstrap_project(...)`.
3. Load env via `project_runtime.load_project_env(...)` instead of duplicating `local_config.sh` parsing.
4. Resolve config or prompt paths relative to project root.

## 5. Telegram Bot Commands
When adding a new command to the listener:
1. **Handler**: Implement the logic in `TelegramUpdateHandler`.
2. **Authorization**: Preserve the current owner chat check.
3. **Testing**: Update `tests/test_telegram_listener.py` to match the actual message flow.

## 6. Error Handling & Strict Policy
To ensure system stability and transparency:
1.  **No Silent Fallbacks**: Never implement logic that silently switches to a default configuration or model (e.g., switching AI models or prompt templates) when a preferred choice fails.
2.  **Immediate Notification**: Critical errors in any pipeline stage (Download, Transcribe, Summarize, Notify) must be logged with technical context and immediately sent to the user via Telegram (`send_telegram_msg`).
3.  **Path Resolution**: Always resolve relative paths against the project root to ensure they work correctly under all execution environments.
4.  **Gemini CLI Trust**: When calling the Gemini CLI in automated or non-interactive environments, always include the `--skip-trust` flag or set the `GEMINI_CLI_TRUST_WORKSPACE=true` environment variable to prevent "not running in a trusted directory" errors.
