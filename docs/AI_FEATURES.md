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
系統採用 Gemini API 作為預設摘要引擎，API key 從 `config/local_config.sh` 載入，不會寫死在程式碼內。

1.  **Gemini API (預設)**: 使用 `GEMINI_API_KEY` 呼叫 Google Generative Language API，預設模型為 `gemini-flash-latest`。
2.  **Ollama (選用 fallback)**: 若設定 `ENABLE_OLLAMA="1"`，Gemini 不可用或沒有輸出時可接著嘗試本地模型（預設為 `qwen2.5:7b`）。
3.  **OpenCode CLI (選用 fallback)**: 若設定 `ENABLE_OPENCODE="1"`，可再接著嘗試 OpenCode CLI。

## 核心流程

1.  **快取檢查**: 檢查是否已有最新的 `.summary.md` 檔案，避免重複扣除 AI 配額。
2.  **輸入優化**: 優先使用繁體中文逐字稿 (`.zh-Hant.txt`) 作為輸入。
3.  **自動繁體化**: 不論模型原生產出的是簡體還是繁體，系統在存檔前都會自動透過 **OpenCC** 進行繁體化，確保最終輸出的一致性。
4.  **發送通知**: 將 Markdown 摘要作為 Email 內文或 Telegram 訊息發送。

## 自訂 Prompt

您可以在 `prompts/` 資料夾下建立專屬的 Markdown 檔案，內容建議包含 `{transcript_content}` 佔位符。摘要時會讀取 prompt 檔案，將該佔位符替換成逐字稿內容，然後把完整 prompt 送到 Gemini API。

### 綁定 Prompt

在 `config/subscriptions.json` 中為特定節目指定 Prompt：
```json
{
  "podcast_url": "...",
  "prompt_file": "prompts/zhaohua.md"
}
```
若未指定，預設使用 `prompts/default.md`。
