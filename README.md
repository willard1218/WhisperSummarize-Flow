# WhisperSummarize-Flow

自動化 Podcast 與 YouTube 直播逐字稿提取、AI 摘要與通知系統。

## 快速開始 (Quick Start)

立刻手動跑今天全部任務：

```bash
./schedule/run_soundon_daily.sh
```

## 核心流程

1. **下載**: 自動追蹤已註冊的 Podcast RSS 或 YouTube 頻道。
2. **轉錄**: 透過 `gensrt.sh` 進行語音轉文字。
3. **處理**: 簡繁轉換 (OpenCC) 與 AI 摘要 (Gemini)。
4. **通知**: 透過 Telegram、SMTP 或 Apple Mail 發送結果。

## 詳細文件 (Documentation)

為了提升 AI 處理效率，本文件已拆分為多個模組：

- [**安裝需求 (Install)**](docs/INSTALL.md): 環境依賴與工具安裝。
- [**設定指南 (Config)**](docs/CONFIG.md): 如何設定私有環境、通知與訂閱。
- [**腳本使用 (Usage)**](docs/SCRIPTS.md): 各腳本參數與常用指令範例。
- [**專案架構 (Architecture)**](docs/ARCHITECTURE.md): 設計理念、跨平台部署與簡繁轉換。
- [**AI 摘要 (AI Features)**](docs/AI_FEATURES.md): Gemini AI 整合與自訂 Prompt 說明。

---

*如有開發需求或規則變更，請參考 [GEMINI.md](GEMINI.md)。*
