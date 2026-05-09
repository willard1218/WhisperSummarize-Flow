# AI 摘要功能 (Gemini AI)

專案整合了 Gemini CLI，能自動對逐字稿進行分析與總結。

## 執行流程

1. 檢查是否存在繁體中文逐字稿 (`.zh-Hant.txt`)。
2. 呼叫 `gemini` 指令。
3. 優先使用 `gemini-3-pro-preview` 模型；若失敗（如 Quota 限制）則自動退回使用 `gemini-3-flash-preview`。
4. 將產生的 Markdown 摘要作為 Email 內文寄出，或透過 Telegram 發送。

## 自訂 Prompt

您可以在 `prompts/` 下建立專屬的 Markdown 檔案。

### 綁定 Prompt

在訂閱設定檔中加入 `prompt_file`:
```json
{
  "podcast_url": "...",
  "prompt_file": "prompts/gooaye.md"
}
```
若未指定，預設使用 `prompts/default.md`。
