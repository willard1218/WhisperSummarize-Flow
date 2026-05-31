# 專案架構與進階設定 (Architecture)

本文件以目前程式碼為準，描述真實執行路徑，而不是理想化的未來設計。

## 目前架構判讀

這個專案本質上是「以 `run_daily_pipeline.py` 為核心的流程型 Python 專案」：

- `pipeline/` 放主要流程與來源同步邏輯。
- `tools/` 放通知、摘要、路徑、設定、listener 與輔助工具。
- `project_runtime.py` 負責統一 bootstrap 專案 root、env 載入與相對路徑解析。

目前不是完整的 framework-style application，也不是完全 plugin-driven system。對 AI 維護最重要的是先理解主入口與資料流，而不是先假設所有模組都已經抽象化完成。

## 真實入口

- `pipeline/run_daily_pipeline.py`
  - 主 orchestrator。
  - 解析 CLI args。
  - 載入 `config/local_config.sh`。
  - 建立 `DailyItem`。
  - 執行 download -> transcribe -> traditionalize -> summarize -> notify。
- `tools/telegram_listener.py`
  - Telegram long polling 入口。
  - 收到 URL 或媒體後直接啟動 `run_daily_pipeline.py`。
  - `/status` 會回報 lock 狀態並呼叫 `tools/check_daily_status.py`。
- `pipeline/run_registered_podcasts.py`
  - Podcast 訂閱與單集下載整合層。
- `pipeline/run_registered_youtube.py`
  - YouTube 頻道最新影片解析與下載整合層。

## 主要資料模型與邊界

- `DailyItem`
  - 單一處理任務的核心資料結構。
  - 保存來源 URL、輸出目錄、轉錄檔、摘要文字、寄送狀態。
- `PipelineContext`
  - 保存 args、task logger 與整體成功狀態。
- `BaseDownloader`
  - 目前實作為 `YouTubeDownloader`、`PodcastDownloader`。
  - 採 `BaseDownloader.__subclasses__()` 發現，不是外部 plugin registry。
- `BaseTranscriber`
  - 目前有 `WhisperKitTranscriber`、`WhisperCPPTranscriber`。
- `BaseNotifier`
  - 目前有 `MailNotifier`、`TelegramNotifier`。

## Bootstrap 與路徑策略

所有核心入口現在應遵守同一套 runtime 規則：

- 專案 root 由 `project_runtime.bootstrap_project(...)` 加入 `sys.path`。
- 環境變數由 `project_runtime.load_project_env(...)` 統一載入 `config/local_config.sh`。
- 相對設定檔路徑由 `project_runtime.resolve_project_path(...)` 解析，避免因 launchd、cron、手動執行目錄不同而出錯。

這一層對 AI 很重要，因為多數「在我機器上可以、換一個入口就壞掉」的 bug 都來自 bootstrap 與 path resolution。

## Output 目錄策略

為了避免不同來源的檔案互相混雜，所有輸出路徑現在都透過共用 path policy 產生：

- `output/podcast/<podcast_title>/`
  - `podcast_title` 來自 `subscriptions.json` 的 `podcast_title`
- `output/youtube/<channel_name>/`
  - `channel_name` 優先取 YouTube `@handle`
- `output/telegram/youtube/<channel_name>/<video_id>/`
  - 為優化管理，Telegram 傳入的連結現在會自動識別頻道名稱並建立次目錄。
- `output/telegram/audio/<task_id>/`
- `output/telegram/video/<task_id>/`
- `output/telegram/youtube/<video_id>/`
- `output/telegram/apple_podcast/<podcast_id>/`
- `output/telegram/soundon_podcast/<episode_uuid>/`

Telegram 任務資料夾會附帶 `metadata.json`，記錄來源 URL、chat id、建立時間與檔案資訊，方便後續重跑、補寄與除錯。

## 轉錄與字幕優化 (Transcription Logic)

- **全域線性化時間軸 (Global Linearization)**: 為解決 WhisperKit 在重疊語音或背景雜訊下產生的時間軸跳躍問題，系統實作了全域線性重建演算法。它以大片段（Segment）為錨點，依據字數比例重新分配單字級時間戳，確保 SRT 100% 單調遞增且不跳轉。
- **語法感知斷句 (Grammatical Cohesion)**: 切割 SRT 時會主動偵測標點符號與虛詞（如「一個」、「的」、「是」），並執行「強制黏著」邏輯，確保每一行字幕語意完整，避免行首孤字（Orphan characters）。
- **自動清理機制**: 為節省空間，`WhisperKitTranscriber` 會在轉錄完成後自動刪除中間產生的 `.wav` 檔案（若該檔案非原始來源）。

## Telegram Listener 分層

`tools/telegram_listener.py` 已依職責切分為數個元件：

- `ListenerSettings`: 封裝 bot token、工作目錄與授權 chat id。
- `TelegramApiClient`: 單獨負責 Telegram Bot API 呼叫，具備超時重試機制。
- `TranscriptionStatusProvider`: 只負責判斷目前轉錄鎖狀態。
- `PipelineLauncher`: 單獨組裝並啟動 `run_daily_pipeline.py`。
- `TelegramFileDownloader`: 專責下載 Telegram 檔案。
- `MessageInterpreter`: 專責解析支援的網址（YouTube/SoundOn/Apple Podcast）與媒體訊息。
- `TelegramUpdateHandler`: 組合上述服務，處理單筆 update。實作「收到連結後排隊執行」、「重複網址優先回傳既有摘要」與「隊列狀態回報」。
- `Registry (Task Queue)`: 使用 `tasks.db` 實作持久化任務隊列，確保大量請求或系統重啟時不漏單。
- `TaskWorker (Background)`: 獨立的背景執行緒，按順序消化隊列，具備「自癒式鎖清理」與「資料庫斷路器」機制。
- `TelegramPoller`: 專責長輪詢與 update offset 推進。

這樣的拆分讓 listener 可以在不依賴真實 Telegram 服務與 Whisper 的情況下進行單元測試。

## 對 AI 維護最重要的現況

- 文件若提到更完整的 plugin/stage framework，請先回頭核對程式碼。
- `run_daily_pipeline.py` 目前仍然偏胖，屬於高風險修改區。
- `tools/config_models.py` 與 `tools/registry.py` 已存在，但尚未成為全專案唯一設定/狀態來源。
- `tests/test_telegram_listener.py` 若與 listener 行為不一致，應先以實作為準，再決定要修測試還是恢復舊互動流程。

## 執行模式

- **Release Mode**: 預設模式，依照註冊檔寄送給所有收件人。
- **Debug Mode**: 使用 `--debug` 參數，強制將所有輸出重新導向至開發者設定的測試信箱與 Telegram ID。

## 跨平台部署

- **SMTP 優先**: 系統統一使用 SMTP 發送郵件，不依賴平台原生郵件 App，確保 macOS/Linux/Windows 行為一致。
- **背景執行**: 在 macOS 下預設使用 `caffeinate` 防止休眠，支援 `launchd` 排程。

## 效能與快取

- **摘要快取**: 系統會檢查 `.summary.md` 是否已存在且比原始逐字稿更新，若是則跳過 AI 呼叫，節省配額。
- **簡繁轉換**: 優先尋找並使用 `.zh-Hant` 繁體檔案，減少重複轉換。

## 監控與日誌 (Logging & Monitoring)

- **AI 友善日誌**: 使用 `tools/logger.py` 實作結構化 KV 日誌，方便 AI Agent 自動分析。
- **日誌輪轉 (Rotation)**: 採用 `RotatingFileHandler` 限制日誌大小（預設 10MB），避免佔滿磁碟。
- **主動警報**: 流程中任何階段（下載、轉錄、摘要）發生嚴重錯誤時，系統會立即透過 Telegram (`send_telegram_msg`) 發送通知。
- **環境檢查**: `tools/health_check.py` 可自動驗證所有外部依賴、Python 套件與權限，作為系統啟動前的第一道防線。
