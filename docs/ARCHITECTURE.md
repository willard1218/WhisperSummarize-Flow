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
- **Tools (`tools/`)**: 提供底層工具支援，如 AI 摘要器、簡繁轉換、通知發送器。
- **防止重複**: 每個收件人/通知管道都有獨立的 `.mail-sent` 標記檔案，確保同一集節目不會重複發送。

## 執行模式

- **Release Mode**: 預設模式，依照註冊檔寄送給所有收件人。
- **Debug Mode**: 使用 `--debug` 參數，強制將所有輸出重新導向至開發者設定的測試信箱與 Telegram ID。

## 跨平台部署

- **SMTP 優先**: 系統統一使用 SMTP 發送郵件，不依賴平台原生郵件 App，確保 macOS/Linux/Windows 行為一致。
- **背景執行**: 在 macOS 下預設使用 `caffeinate` 防止休眠，支援 `launchd` 排程。

## 效能與快取

- **摘要快取**: 系統會檢查 `.summary.md` 是否已存在且比原始逐字稿更新，若是則跳過 AI 呼叫，節省配額。
- **簡繁轉換**: 優先尋找並使用 `.zh-Hant` 繁體檔案，減少重複轉換。
