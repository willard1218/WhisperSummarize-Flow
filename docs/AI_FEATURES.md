# AI 核心功能 (AI Features)

本專案整合了多種 AI 技術，涵蓋語音轉文字（轉錄）與文字摘要。

## 轉錄引擎 (Transcription) —— **新功能！**

系統支援多種轉錄後端，可透過 `--transcriber-type` 參數切換：

1.  **WhisperKit (預設)**: 針對 Apple Silicon (M1/M2/M3) 優化的轉錄引擎。
    *   **優點**: 速度極快（利用 GPU/Neural Engine），支援**語者辨識 (Diarization)**。
    *   **產出**: 自動區分主持人與來賓（標註為 SPEAKER 1, SPEAKER 2...）。
2.  **Whisper.cpp (`gensrt.sh`)**: 跨平台的 C/C++ 實作。
    *   **優點**: 資源佔用低，支援多種 Model 尺寸（如 `large-v3`）。
    *   **限制**: 目前暫不支援語者辨識。

## 摘要功能 (Summarization)
...
系統採用優先序機制，可透過環境變數動態切換：

1.  **Ollama (本地)**: 若設定 `ENABLE_OLLAMA="1"`，則優先使用本地模型（預設為 `qwen2.5:7b`）。這對於保護隱私與節省雲端配額非常有用。
2.  **Gemini (雲端)**: 預設摘要引擎。
    *   優先使用 `gemini-3-pro-preview` 模型。
    *   若 Pro 模型失敗（如 Quota 限制），自動退回使用 `gemini-3-flash-preview`。

## 核心流程

1.  **快取檢查**: 檢查是否已有最新的 `.summary.md` 檔案，避免重複扣除 AI 配額。
2.  **輸入優化**: 優先使用繁體中文逐字稿 (`.zh-Hant.txt`) 作為輸入。
3.  **自動繁體化**: 不論模型原生產出的是簡體還是繁體，系統在存檔前都會自動透過 **OpenCC** 進行繁體化，確保最終輸出的一致性。
4.  **發送通知**: 將 Markdown 摘要作為 Email 內文或 Telegram 訊息發送。

## 自訂 Prompt

您可以在 `prompts/` 資料夾下建立專屬的 Markdown 檔案，內容必須包含 `{transcript_content}` 佔位符。

### 綁定 Prompt

在 `config/subscriptions.json` 中為特定節目指定 Prompt：
```json
{
  "podcast_url": "...",
  "prompt_file": "prompts/zhaohua.md"
}
```
若未指定，預設使用 `prompts/default.md`。
