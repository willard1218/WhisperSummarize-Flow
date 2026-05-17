# 腳本使用說明 (Scripts & Usage)

## 主要進入點

### `pipeline/run_daily_pipeline.py`
執行自動化流程的核心腳本。支援「訂閱模式」與「一次性任務模式」。

#### 常規參數
- `--date YYYY-MM-DD`: 指定日期（預設為今天）。系統會檢查該日期是否有新節目。
- `--debug`: 調試模式。會強制將所有郵件與 Telegram 訊息重新導向至開發者設定的測試帳號，並在當天無更新時自動抓取最新一集。

#### 一次性任務 (Ad-hoc) —— **新功能！**
若您只想處理某個特定影片或音檔，而不打算長期訂閱，可以使用此參數：
- `--url 'URL'`: 指定 YouTube 影片網址或 Podcast 單集網址。
- `--local-file 'PATH'`: 指定本地音訊檔案路徑（跳過下載階段）。
- `--recipient-group 'GROUP'`: 指定收件人群組（預設為 `all`）。
- `--transcriber-type [whisperkit|whispercpp]`: 指定轉錄引擎（預設為 `whisperkit`）。

**使用範例：**
```bash
# 處理單一 YouTube 影片並發送給所有人
python3 pipeline/run_daily_pipeline.py --url "https://youtube.com/watch?v=VIDEO_ID"

# 處理本地錄音檔並寄送至指定群組
python3 pipeline/run_daily_pipeline.py --local-file "./my_voice.m4a" --recipient-group personal
```

## 註冊工具 (長期訂閱)

### `tools/register_podcast.py`
將 Podcast 頻道加入每日監控清單。
```bash
python3 tools/register_podcast.py 'URL' --recipient-group 'GROUP'
```

### `tools/register_youtube_channel.py`
將 YouTube 頻道加入每日監控清單。
```bash
python3 tools/register_youtube_channel.py 'URL' --recipient-group 'GROUP'
```

## 其他實用腳本

- `tools/check_daily_status.py`: **即時狀態查詢工具**。快速掃描今日所有頻道（Podcast, YouTube, Telegram）的下載、轉錄、摘要與寄信進度。
- `tools/dump_daily_plan.py`: 預覽當日執行計畫（查看誰會被下載、誰會被通知）。
- `tools/telegram_listener.py`: **Telegram Bot 互動監聽器**。
  - **URL 啟動**：直接將 YouTube、SoundOn 或 Apple Podcast 網址傳給 Bot。Bot 會先回覆確認按鈕；按下「確認執行」後才會建立任務。具備「長網址自動轉換 ID」機制以符合 Telegram 限制。
  - **忙碌狀態**：若目前已有轉錄工作持有 lock，Bot 會顯示「忙碌中」，新任務仍可確認並排隊。
  - **媒體轉錄**：直接傳送「語音訊息」、「音訊檔案」或「視訊檔案」給 Bot，它會自動下載並開始轉錄。
  - **單一執行實例 (Singleton)**：使用 `/tmp/telegram_listener.pid` 確保同一時間只有一個 listener 在執行，防止重複處理訊息及 409 Conflict 錯誤。
  - **詳細日誌**：所有互動、API 呼叫與錯誤都會記錄在 `logs/telegram_listener.log`，方便排除「傳一次執行兩次」等問題。
  - **授權限制**：若 `TELEGRAM_CHAT_ID` 有設定，只有該 chat 可觸發流程。
- `schedule/update_schedule.sh`: 修改 `launchd` 排程時間（macOS）。
- `gensrt.sh`: 核心轉錄引擎。整合了 Whisper 與全域鎖，確保轉錄過程不衝突且具備斷點續傳能力。

## 輸出目錄規則

- 訂閱 Podcast: `output/podcast/<podcast_title>/`
- 訂閱 YouTube: `output/youtube/<channel_name>/`
- Telegram 音檔: `output/telegram/audio/<task_id>/`
- Telegram 影片檔: `output/telegram/video/<task_id>/`
- Telegram YouTube 網址: `output/telegram/youtube/<video_id>/`
- Telegram Apple Podcast 網址: `output/telegram/apple_podcast/<podcast_id>/`
- Telegram SoundOn 網址: `output/telegram/soundon_podcast/<episode_uuid>/`

同一任務的音檔、逐字稿、摘要、寄送標記會集中放在同一個任務資料夾內；Telegram 任務另外會保存 `metadata.json`。
