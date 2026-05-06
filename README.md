# soundon_rss

這套流程現在支援兩種來源，會每天自動下載、轉逐字稿、再用 Apple Mail 寄出：

- podcast
- YouTube 直播頻道

目前的設計重點是：

- podcast 跟 YouTube 都用註冊檔管理，不用改程式碼。
- daily runner 會先下載所有 podcast / YouTube，再集中轉錄，再集中寄信。
- `gensrt.sh` 有全域鎖，同一時間只會有一個轉錄在跑。
- 每次 daily runner 會自動用 `caffeinate` 包住，避免轉錄中途休眠。
- 每個收件人都會各自留下寄送標記，避免重複寄同一份逐字稿。
- 如果開啟 `OPENCC_TRADITIONALIZE=1`，轉錄完成後會針對 `.srt.txt` 額外產生一份繁中逐字稿並優先寄出。

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
  設定本機 `GENSRT_SCRIPT`、`PYTHON_BIN`、`OPENCC_*`
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

## 專案架構

本專案採用模組化設計，確保邏輯的一致性：

- **核心模組**：`run_registered_podcasts.py` 與 `run_registered_youtube.py` 封裝了各自來源的下載、路徑解析與郵件邏輯。
- **整合入口**：`run_daily_pipeline.py` 作為主要的自動化入口， import 核心模組的函式並實作「分階段執行」（下載 -> 轉錄 -> 寄信），以提升穩定性。
- **共用工具**：`recipient_groups.py` 處理收件人群組解析；`convert_transcript_opencc.py` 負責繁簡轉換。

## 註冊 podcast

註冊一個 podcast，直接綁定本機收件人群組：

```bash
python3 register_podcast.py \
  'PODCAST_URL' \
  --recipient-group your_group_name
```

## 註冊 YouTube 頻道

註冊一個 YouTube 直播頻道，直接綁定本機收件人群組：

```bash
python3 register_youtube_channel.py \
  'https://www.youtube.com/@channelname/streams' \
  --recipient-group your_group_name
```

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

## 設定每天執行時間

例如改成每天 `16:00`：

```bash
./launchd/update_schedule.sh 16 00
```

## 手動立即執行

立刻手動跑今天全部任務：

```bash
./launchd/run_soundon_daily.sh
```
