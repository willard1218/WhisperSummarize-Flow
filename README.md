# soundon_rss

這套流程現在支援兩種來源，會每天自動下載、轉逐字稿、再用 Apple Mail 寄出：

- podcast
- YouTube 直播頻道

目前的設計重點是：

- podcast 跟 YouTube 都用註冊檔管理，不用改程式碼。
- daily runner 會先下載所有 podcast / YouTube，再集中轉錄、繁簡轉換、AI 摘要，最後集中寄信。
- `gensrt.sh` 有全域鎖，同一時間只會有一個轉錄在跑。
- 每次 daily runner 會自動用 `caffeinate` 包住，避免轉錄中途休眠。
- 每個收件人都會各自留下寄送標記，避免重複寄同一份逐字稿。
- 如果開啟 `OPENCC_TRADITIONALIZE=1`，轉錄完成後會針對 `.srt.txt` 與 `.txt` 額外產生繁中版本。
- 支援透過 Gemini CLI 自動為逐字稿產生 Markdown 摘要，並作為信件內文寄出。

## 需求

- macOS
- `python3`
- `yt-dlp`
- Apple Mail 已登入且可正常寄信
- 逐字稿腳本 `gensrt.sh`
- `ffmpeg` 與 `gensrt.sh` 依賴的轉錄環境
- 如果要做本地簡轉繁：`opencc`

## 本機私有設定

先建立本機私有設定：

```bash
cp local_config.example.sh local_config.sh
cp recipient_groups.example.json recipient_groups.local.json
cp subscriptions.example.json subscriptions.json
cp youtube_subscriptions.example.json youtube_subscriptions.json
```

- `local_config.sh`
  設定本機 `GENSRT_SCRIPT`、`PYTHON_BIN`、`OPENCC_*`，以及 **SMTP 寄信設定**（見下文）。

### 寄信方式設定

本專案支援兩種寄信方式：

1. **Apple Mail (預設)**: 透過 AppleScript 驅動系統內建 Mail app。不需額外設定，但需保持 Mail app 登入。
2. **SMTP (推薦)**: 直接透過伺服器背景寄信，更穩定且不需啟動 Mail app。

若要使用 **iCloud SMTP**，請在 `local_config.sh` 加入：
```bash
SMTP_HOST="smtp.mail.me.com"
SMTP_PORT="587"
SMTP_USER="你的iCloud信箱"
SMTP_PASS="你的App專用密碼"
SMTP_FROM="顯示的寄件者信箱"
```
*註：iCloud 密碼必須使用在 appleid.apple.com 申請的「App 專用密碼」。*
- `recipient_groups.local.json`
  設定本機私有收件人群組
- `subscriptions.json`
  Podcast 訂閱清單
- `youtube_subscriptions.json`
  YouTube 訂閱清單

私人 email 請放在本機私有檔：

`recipient_groups.local.json`

這個檔案已被 `.gitignore` 忽略，不會被 `git add`。

## 常用腳本

- `register_podcast.py`
  註冊 podcast 網址和收件人 email 或本機群組。
- `register_youtube_channel.py`
  註冊 YouTube 頻道網址和收件人 email 或本機群組。
- `run_daily_pipeline.py`
  每日三階段流程：全部下載、全部轉錄、全部寄信。
- `run_registered_podcasts.py`
  手動跑一次所有已註冊的 podcast。
- `run_registered_youtube.py`
  手動跑一次所有已註冊的 YouTube 頻道。
- `dump_daily_plan.py`
  列出每天爬取順序，以及每個來源展開後會寄給哪些 email。
- `download_and_transcribe_latest.sh`
  單獨抓某個 YouTube 頻道最新一支已結束直播。
- `launchd/run_soundon_daily.sh`
  每日排程真正執行的入口。
- `launchd/update_schedule.sh`
  修改每天自動執行時間。

## 常用用法

### register_podcast.py

用法：

```bash
python3 register_podcast.py PODCAST_URL [EMAIL ...] [--recipient-group GROUP] [--config FILE]
```

例如直接綁定本機群組：

```bash
python3 register_podcast.py \
  'PODCAST_URL' \
  --recipient-group your_group_name
```

例如直接指定收件人：

```bash
python3 register_podcast.py \
  'PODCAST_URL' \
  alice@example.com bob@example.com
```

### register_youtube_channel.py

用法：

```bash
python3 register_youtube_channel.py CHANNEL_URL [EMAIL ...] [--recipient-group GROUP] [--config FILE]
```

例如綁定本機群組：

```bash
python3 register_youtube_channel.py \
  'https://www.youtube.com/@channelname/streams' \
  --recipient-group your_group_name
```

### run_daily_pipeline.py

用法：

```bash
python3 run_daily_pipeline.py [--date YYYY-MM-DD] [--output-root DIR] [--debug]
```

- `--date`：指定要處理哪一天，預設是今天
- `--debug`：只寄給 `DEBUG_RECIPIENT`

例如跑今天全部任務：

```bash
python3 run_daily_pipeline.py
```

例如重跑某一天：

```bash
python3 run_daily_pipeline.py --date 2026-05-07
```

### run_registered_podcasts.py

用法：

```bash
python3 run_registered_podcasts.py [--date YYYY-MM-DD] [--output-root DIR] [--debug]
```

例如只跑 podcast：

```bash
python3 run_registered_podcasts.py --date 2026-05-07
```

### run_registered_youtube.py

用法：

```bash
python3 run_registered_youtube.py [--output-root DIR] [--debug]
```

例如只跑 YouTube：

```bash
python3 run_registered_youtube.py
```

### dump_daily_plan.py

用法：

```bash
python3 dump_daily_plan.py [--format text|json] [--show-urls] [--show-groups] [--show-prompts]
```

例如列出來源網址和群組、Prompt設定：

```bash
python3 dump_daily_plan.py --show-urls --show-groups --show-prompts
```

### download_latest_podcast.py

用法：

```bash
python3 download_latest_podcast.py PODCAST_URL [-o DIR] [--episode-date YYYY-MM-DD] [--transcribe-script SCRIPT]
```

例如抓指定日期那一集：

```bash
python3 download_latest_podcast.py \
  'PODCAST_URL' \
  -o output/tmp \
  --episode-date 2026-05-07
```

### download_and_transcribe_latest.sh

用法：

```bash
./download_and_transcribe_latest.sh CHANNEL_URL [OUTPUT_DIR] [MAIL_RECIPIENTS] [TRANSCRIBE_SCRIPT]
```

例如抓某個 YouTube 頻道最新一支已結束直播：

```bash
./download_and_transcribe_latest.sh \
  'https://www.youtube.com/@channelname/streams' \
  output/tmp \
  'alice@example.com,bob@example.com'
```

### convert_transcript_opencc.py

用法：

```bash
python3 convert_transcript_opencc.py INPUT_PATH [--output-path FILE] [--config OPENCC_CONFIG]
```

例如手動把逐字稿轉成繁中：

```bash
python3 convert_transcript_opencc.py \
  output/example.srt.txt \
  --output-path output/example.zh-Hant.srt.txt
```

### launchd/update_schedule.sh

用法：

```bash
./launchd/update_schedule.sh HOUR MINUTE
./launchd/update_schedule.sh START_HOUR END_HOUR MINUTE
```

- `HOUR`：24 小時制，範圍 `0-23`
- `START_HOUR` / `END_HOUR`：整點重跑區間，範圍 `0-23`
- `MINUTE`：分鐘，範圍 `0-59`

例如改成每天 `16:00`：

```bash
./launchd/update_schedule.sh 16 00
```

例如改成每天從 `15:00` 到 `23:00` 每小時跑一次：

```bash
./launchd/update_schedule.sh 15 23 00
```

## 專案架構

本專案採用模組化設計，確保邏輯的一致性：

- **核心模組**：`run_registered_podcasts.py` 與 `run_registered_youtube.py` 封裝了各自來源的下載、路徑解析與郵件邏輯。
- **整合入口**：`run_daily_pipeline.py` 作為主要的自動化入口， import 核心模組的函式並實作「分階段執行」（下載 -> 轉錄 -> 寄信），以提升穩定性。
- **共用工具**：`recipient_groups.py` 處理收件人群組解析；`convert_transcript_opencc.py` 負責繁簡轉換。

## 執行模式

為了方便測試而不影響正式訂閱者，腳本支援兩種模式：

### 1. Release Mode (日常營運模式)
這是預設模式。系統會依照註冊檔中的設定，將逐字稿寄送給所有指定的收件人。
```bash
python3 run_daily_pipeline.py
```

### 2. Debug Mode (調試模式)
加上 `--debug` 參數後，系統會**忽略**註冊檔中的收件人設定，強制僅寄送至環境變數 `DEBUG_RECIPIENT` 中設定的地址。
```bash
python3 run_daily_pipeline.py --debug
```
請確保在 `local_config.sh` 中設定了：
```bash
DEBUG_RECIPIENT="yourname@example.com"
```

## 跨平台部署 (Windows / Linux)

本專案的核心 Python 程式（`run_daily_pipeline.py`）是跨平台的，**不依賴特定的排程工具**。

1. **依賴項**：確保系統已安裝 `python3`、`yt-dlp`、`ffmpeg`。
2. **寄信設定**：非 macOS 系統**不支援 Apple Mail 寄信**，必須在環境變數或啟動腳本中提供完整的 SMTP 設定。
3. **排程設定**：
   - **macOS**：使用專案內提供的 `launchd/run_soundon_daily.sh`。
   - **Windows**：請建立一個批次檔（`.bat`）來設定環境變數並執行 `python run_daily_pipeline.py`。然後使用「Windows 工作排程器 (Task Scheduler)」設定每日執行，並在排程條件中勾選「喚醒電腦以執行此工作」來取代 macOS 的 `caffeinate` 防休眠機制。
   - **Linux**：可使用 `cron` 或 `systemd` 搭配 Bash 腳本執行。

## 簡轉繁規則

如果你想在逐字稿完成後，用本機 OpenCC 把簡體中文轉成繁體中文：

1. 在 `local_config.sh` 打開開關：

```bash
OPENCC_TRADITIONALIZE="1"
OPENCC_CONFIG="s2twp.json"
```

2. daily pipeline 會在轉錄完成後，針對 `.srt.txt` 額外產生繁中版本：

- `foo.srt.txt` -> `foo.zh-Hant.srt.txt`

預設會用 `s2twp.json`，也就是偏台灣用字的簡轉繁設定。

## 手動立即執行

立刻手動跑今天全部任務：

```bash
./launchd/run_soundon_daily.sh
```
mail 的內文寄出。

### 1. 安裝與設定
請確保系統已安裝 `gemini` CLI 工具，且已完成登入認證。

### 2. 自訂 Prompt
由於每個頻道/Podcast 的內容結構不同（如有來賓對談、單純技術分析、有閒聊與 QA 等），你可以在 `prompts/` 資料夾下建立專屬的 Markdown Prompt 檔案（例如 `gooaye.md`, `zhaohua.md`, `youtube_tech.md`）。

### 3. 綁定 Prompt 到頻道
在註冊頻道的設定檔 (`subscriptions.json` 或 `youtube_subscriptions.json`) 中，加入 `prompt_file` 屬性來指定該頻道要使用的 Prompt 檔案：

```json
{
  "subscriptions": [
    {
      "podcast_url": "...",
      "rss_url": "...",
      "recipient_group": "your_group_name",
      "prompt_file": "prompts/zhaohua.md"
    }
  ]
}
```
若未指定 `prompt_file`，系統會預設使用 `prompts/default.md` 進行摘要。

## 手動立即執行

立刻手動跑今天全部任務：

```bash
./launchd/run_soundon_daily.sh
```
