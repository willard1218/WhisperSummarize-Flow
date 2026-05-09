# 腳本使用說明 (Scripts & Usage)

## 主要進入點

### `pipeline/run_daily_pipeline.py`
每日自動化流程：下載 -> 轉錄 -> 摘要 -> 通知。
- `--date YYYY-MM-DD`: 指定日期（預設今天）。
- `--debug`: 調試模式，僅通知給 `DEBUG_RECIPIENT` 與 `DEBUG_TELEGRAM_CHAT_ID`。

## 註冊工具

### `tools/register_podcast.py`
註冊 Podcast。
```bash
python3 tools/register_podcast.py 'URL' --recipient-group 'GROUP'
```

### `tools/register_youtube_channel.py`
註冊 YouTube 頻道。
```bash
python3 tools/register_youtube_channel.py 'URL' --recipient-group 'GROUP'
```

## 其他實用腳本

- `pipeline/run_registered_podcasts.py`: 僅跑 Podcast。
- `pipeline/run_registered_youtube.py`: 僅跑 YouTube。
- `tools/dump_daily_plan.py`: 預覽當日執行計畫。
- `schedule/update_schedule.sh`: 修改 `launchd` 排程時間。
- `pipeline/download_and_transcribe_latest.sh`: 單獨處理特定頻道的最新影片。
