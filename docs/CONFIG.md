# 設定指南 (Configuration)

本專案使用環境變數與私有設定檔來管理敏感資訊與執行參數。

## 初始設定

請先從範例檔案複製出本機設定：

```bash
cp config/local_config.example.sh config/local_config.sh
cp config/recipient_groups.example.json config/recipient_groups.local.json
cp config/subscriptions.example.json config/subscriptions.json
cp config/youtube_subscriptions.example.json config/youtube_subscriptions.json
```

## 設定檔說明

### 1. `config/local_config.sh`
這是最重要的設定檔，包含路徑、階段開關以及通知方式。

#### 管線階段控制 (Toggles)
- `ENABLE_TRANSCRIBE`: `"1"` 執行轉錄，`"0"` 略過（若音檔已存在）。
- `ENABLE_TRADITIONALIZE`: `"1"` 執行簡轉繁，`"0"` 關閉。
- `ENABLE_SUMMARIZE`: `"1"` 執行 AI 摘要。
- `ENABLE_OLLAMA`: `"1"` 開啟本地 Ollama 作為 Gemini API 後的 fallback。
- `ENABLE_OPENCODE`: `"1"` 開啟 OpenCode CLI 作為額外 fallback。
- `ENABLE_MAIL`, `ENABLE_TELEGRAM`: 是否開啟通知。

#### Gemini API 摘要
摘要預設透過 Gemini HTTP API 執行，請在本機設定檔填入 API key：

```bash
GEMINI_API_KEY="你的GeminiAPIKey"
GEMINI_MODEL="gemini-flash-latest"
GEMINI_TIMEOUT_SECONDS="300"
```

`GEMINI_API_KEY` 不應提交到 git；請只放在 `config/local_config.sh`。

#### 通知與寄信方式
1. **Telegram**:
   ```bash
   ENABLE_TELEGRAM="1"
   TELEGRAM_BOT_TOKEN="你的BotToken"
   TELEGRAM_CHAT_ID="你的ChatID"
   ```
2. **SMTP (強制)**:
   專案現在統一使用 SMTP。請務必填寫以下參數：
   ```bash
   SMTP_HOST="smtp.mail.me.com"
   SMTP_PORT="587"
   SMTP_USER="你的信箱"
   SMTP_PASS="你的App專用密碼"
   ```
3. **全域接收者 (Global Recipients)**:
   如果您希望特定信箱（例如開發者）強制收到**每一封**總結信件，可以使用：
   ```bash
   GLOBAL_RECIPIENTS="developer@example.com"
   ```
   (多個信箱請用逗號分隔)

### 2. `config/recipient_groups.local.json`
定義收件人群組（例如：`all`, `invest`）。您可以為不同節目分配不同的群組。

### 3. `config/subscriptions.json`
定義訂閱的 Podcast RSS 與其對應的 Prompt 範本。

### 4. 轉錄設定 (Whisper)
`gensrt.sh` 需要以下變數，請在 `local_config.sh` 設定：
- `FFMPEG_BIN`: `ffmpeg` 執行檔路徑。
- `WHISPER_BIN`: `whisper.cpp` 的 `main` 程式路徑。
- `WHISPER_MODEL`: 模型路徑（建議使用 `large-v3` 以獲得最佳準確度）。
