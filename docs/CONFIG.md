# 設定指南 (Configuration)

本專案使用本機私有設定檔來管理敏感資訊與執行參數。

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
設定路徑、開關以及通知方式。

#### 通知與寄信方式
1. **Telegram**:
   ```bash
   ENABLE_TELEGRAM="1"
   TELEGRAM_BOT_TOKEN="你的BotToken"
   TELEGRAM_CHAT_ID="你的ChatID"
   ```
2. **SMTP (推薦)**:
   ```bash
   SMTP_HOST="smtp.mail.me.com"
   SMTP_PORT="587"
   SMTP_USER="你的iCloud信箱"
   SMTP_PASS="你的App專用密碼"
   SMTP_FROM="顯示的寄件者信箱"
   ```
3. **Apple Mail (預設)**: 若未設定 SMTP，在 macOS 上會自動回退至 Apple Mail。

### 2. `config/recipient_groups.local.json`
定義收件人群組。此檔案已被 `.gitignore` 忽略，請放心存放私密 Email。

### 4. `gensrt.sh` 轉錄設定
`gensrt.sh` 會讀取以下環境變數，請在 `config/local_config.sh` 中設定：
- `FFMPEG_BIN`: `ffmpeg` 執行檔路徑 (預設: `ffmpeg`)。
- `WHISPER_BIN`: `whisper.cpp` 的 `main` 程式路徑 (預設: `main`)。
- `WHISPER_MODEL`: `whisper.cpp` 的模型檔案路徑 (預設: `models/ggml-large-v3.bin`)。
