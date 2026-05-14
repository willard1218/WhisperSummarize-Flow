# 專案架構與進階設定 (Architecture)

本專案遵循 **SOLID** 設計原則，採用高度解耦的插件式架構，確保系統易於擴充且維護成本低。

## 核心設計原則 (SOLID)

- **單一職責 (SRP)**: 下載、轉錄、摘要、通知均由獨立模組負責。
- **開閉原則 (OCP)**: 
  - **Pipeline Stages**: 管線流程由多個 `BasePipelineStage` 組成，增加新流程只需新增類別，無需修改主循環。
  - **Downloader Plugins**: 支援多樣化的網址模式（頻道網址、單一影片/集數網址），透過繼承 `BaseDownloader` 即可擴充下載邏輯，無需改動管線核心。
  - **RSS Resolvers**: 支援多平台 RSS 解析（SoundOn, Apple Podcasts），透過繼承 `BaseRSSResolver` 即可擴充。
  - **Transcriber Engines**: 透過繼承 `BaseTranscriber` (如 `WhisperKitTranscriber`, `WhisperCPPTranscriber`)，可無縫切換或新增轉錄後端。
  - **Notifiers**: 通知管道（SMTP, Telegram）採用動態發現機制，繼承 `BaseNotifier` 即自動生效。
  - **Summarizers**: 摘要模型（Gemini, Ollama）動態註冊，支援優先序切換。
- **依賴反轉 (DIP)**: 高層管線邏輯依賴於抽象介面，不直接依賴具體的 AI 模型或通知服務。

## 模組化設計

- **Pipeline (`pipeline/`)**: 負責調度各個 `Stage`，處理任務生命週期。
- **Tools (`tools/`)**: 提供底層工具支援，如 AI 摘要器、簡繁轉換、通知發送器與 Telegram Bot 互動。
- **Shared Config Loader (`tools/local_config.py`)**: 統一載入 `config/local_config.sh`，避免不同入口重複實作環境變數解析。
- **Shared Output Path Policy (`tools/output_paths.py`)**: 統一管理訂閱任務與 Telegram ad-hoc 任務的目錄命名規則。
- **防止重複**: 每個收件人/通知管道都有獨立的 `.mail-sent` 標記檔案，確保同一集節目不會重複發送。

## Output 目錄策略

為了避免不同來源的檔案互相混雜，所有輸出路徑現在都透過共用 path policy 產生：

- `output/podcast/<podcast_title>/`
  - `podcast_title` 來自 `subscriptions.json` 的 `podcast_title`
- `output/youtube/<channel_name>/`
  - `channel_name` 優先取 YouTube `@handle`
- `output/telegram/audio/<task_id>/`
- `output/telegram/video/<task_id>/`
- `output/telegram/youtube/<video_id>/`
- `output/telegram/podcast/<podcast_slug>/`

Telegram 任務資料夾會附帶 `metadata.json`，記錄來源 URL、chat id、建立時間與檔案資訊，方便後續重跑、補寄與除錯。

## Telegram Listener 分層

`tools/telegram_listener.py` 已依職責切分為數個元件，避免單一函式同時負責輪詢、解析訊息、下載檔案與啟動 pipeline：

- `ListenerSettings`: 封裝 bot token、工作目錄與授權 chat id。
- `TelegramApiClient`: 單獨負責 Telegram Bot API 呼叫。
- `TranscriptionStatusProvider`: 只負責判斷目前轉錄鎖狀態。
- `PipelineLauncher`: 單獨組裝並啟動 `run_daily_pipeline.py`。
- `TelegramFileDownloader`: 專責下載 Telegram 檔案。
- `MessageInterpreter`: 專責解析支援的網址與媒體訊息。
- `TelegramUpdateHandler`: 組合上述服務，處理單筆 update。
- `TelegramPoller`: 專責長輪詢與 update offset 推進。

這樣的拆分讓 listener 可以在不依賴真實 Telegram 服務與 Whisper 的情況下進行單元測試。

## 執行模式

- **Release Mode**: 預設模式，依照註冊檔寄送給所有收件人。
- **Debug Mode**: 使用 `--debug` 參數，強制將所有輸出重新導向至開發者設定的測試信箱與 Telegram ID。

## 跨平台部署

- **SMTP 優先**: 系統統一使用 SMTP 發送郵件，不依賴平台原生郵件 App，確保 macOS/Linux/Windows 行為一致。
- **背景執行**: 在 macOS 下預設使用 `caffeinate` 防止休眠，支援 `launchd` 排程。

## 效能與快取

- **摘要快取**: 系統會檢查 `.summary.md` 是否已存在且比原始逐字稿更新，若是則跳過 AI 呼叫，節省配額。
- **簡繁轉換**: 優先尋找並使用 `.zh-Hant` 繁體檔案，減少重複轉換。
