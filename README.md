# WhisperSummarize-Flow

自動化 Podcast 與 YouTube 直播逐字稿提取、AI 摘要與通知系統。

## 快速開始 (Quick Start)

立刻手動跑今天全部任務：

```bash
./schedule/run_soundon_daily.sh
```

## 核心流程

1. **環境預檢**: 透過 `tools/health_check.py` 確保所有依賴工具與套件已就緒。
2. **下載/接收**: 自動追蹤已註冊的 Podcast/YouTube，或直接透過 Telegram 傳送 **YouTube/SoundOn 網址**、**語音訊息**、**音訊/視訊檔案**。
3. **轉錄**: 透過 `WhisperKit` (GPU) 或 `gensrt.sh` 進行語音轉文字（支援語者辨識）。
4. **處理**: 簡繁轉換 (OpenCC) 與 AI 摘要 (Gemini)。
5. **通知**: 透過 Telegram、SMTP 或 Apple Mail 發送結果。

## 程式入口與模組邊界

- `pipeline/run_daily_pipeline.py`: 主入口，負責組 task、調度下載/轉錄/摘要/通知。
- `tools/telegram_listener.py`: Telegram long-polling listener，收到支援網址或媒體後直接啟動 pipeline。
- `pipeline/run_registered_podcasts.py`: Podcast 訂閱同步與單集下載整合。
- `pipeline/run_registered_youtube.py`: YouTube 訂閱同步與最新影片下載整合。
- `pipeline/transcribers.py`: `BaseTranscriber`、`WhisperKitTranscriber`、`WhisperCPPTranscriber`。
- `tools/notifier.py`: mail / Telegram notifier 與寄送標記管理。
- `tools/output_paths.py`: 所有 output path policy 與 `metadata.json` 寫入。
- `project_runtime.py`: 專案 root bootstrap、共用 env 載入、相對路徑解析。

目前文件以程式碼為準；若文件與實作不一致，請優先閱讀上述入口模組。

## Output 結構

預設輸出根目錄為 `output/`，並依來源分層：

- `output/podcast/<podcast_title>/`
- `output/youtube/<channel_name>/`
- `output/telegram/audio/<task_id>/`
- `output/telegram/video/<task_id>/`
- `output/telegram/youtube/<video_id>/`
- `output/telegram/apple_podcast/<podcast_id>/`
- `output/telegram/soundon_podcast/<episode_uuid>/`

每個任務資料夾內會集中保存音檔、逐字稿、摘要與寄送標記（包含 `.mail.txt` 主旨備份與 `.mail.srt` 附件備份）；Telegram 任務另外會建立 `metadata.json` 方便追蹤來源。

## 詳細文件 (Documentation)

為了提升 AI 處理效率，本文件已拆分為多個模組：

- [**安裝需求 (Install)**](docs/INSTALL.md): 環境依賴與工具安裝。
- [**設定指南 (Config)**](docs/CONFIG.md): 如何設定私有環境、通知與訂閱。
- [**腳本使用 (Usage)**](docs/SCRIPTS.md): 各腳本參數與常用指令範例。
- [**專案架構 (Architecture)**](docs/ARCHITECTURE.md): 目前真實模組分層、執行流程與 AI 維護重點。
- [**AI 摘要 (AI Features)**](docs/AI_FEATURES.md): Gemini AI 整合與自訂 Prompt 說明。
- [**測試流程 (Testing)**](docs/TESTING.md): 修改後的驗證命令、listener smoke test 與已知測試邊界。

---

*如有開發需求或規則變更，請參考 [GEMINI.md](GEMINI.md)。*
