# 安裝需求 (Requirements)

本專案主要運行於 macOS 環境，但核心邏輯支援跨平台。

## 系統需求

- **作業系統**: macOS (推薦) / Linux / Windows
- **Python**: `python3` (建議 3.10+)
- **工具**:
  - `yt-dlp`: 用於下載 YouTube 影片/直播。
  - `ffmpeg`: 用於音訊處理。
  - `opencc`: (選用) 用於簡繁轉換。

## 服務需求

- **Apple Mail**: (macOS 專用) 已登入且可正常寄信的 Mail app。
- **SMTP**: (推薦) 如果不想依賴 Mail app，需備妥 SMTP 伺服器資訊（如 iCloud, Gmail）。
- **逐字稿腳本**: 專案內附 `gensrt.sh`，需依賴 `whisper.cpp` 的 `main` 執行檔與模型檔案 (`.bin`)。
- **Gemini CLI**: 用於自動生成 AI 摘要。

