# 測試流程 (Testing)

本專案目前使用 Python 標準函式庫 `unittest` 為主，搭配 `trace` 做覆蓋率檢查，不依賴額外測試套件。

## AI / 開發者修改後必跑

每次修改 Python 程式後，至少依序執行：

```bash
python3 -m py_compile \
  tools/local_config.py \
  tools/telegram_listener.py \
  tools/notifier.py \
  tools/recipient_groups.py \
  pipeline/run_daily_pipeline.py \
  pipeline/transcribers.py

python3 -m unittest discover -s tests -v

python3 -m trace --count --summary --module unittest discover -s tests
```

## 這三步各自保證什麼

1. `py_compile`
   - 抓語法錯誤、縮排錯誤、匯入時的基本問題。
2. `unittest`
   - 驗證 listener、通知、收件人解析、transcriber 輸出與 ad-hoc pipeline 建構流程。
3. `trace --summary`
   - 檢查核心模組是否真的被測試跑到，不只是假裝有 testcase。

## Telegram / 背景服務變更時的額外手動驗證

若本次修改涉及 `tools/telegram_listener.py`、`schedule/setup_listener.sh` 或 Telegram Bot 行為，完成單元測試後再做一次手動驗證：

```bash
./schedule/setup_listener.sh
launchctl print gui/$(id -u)/com.whispersummarize.listener | sed -n '1,80p'
```

然後在 Telegram 做以下 smoke test：

1. 傳 `/status`，確認會回覆 `空閒中` 或 `忙碌中`。
2. 傳一個 YouTube 網址，確認會出現「確認執行 / 取消」按鈕。
3. 按下「確認執行」，確認 listener 有啟動 `run_daily_pipeline.py`。
4. 傳一個小型音檔，確認 Bot 會先下載再啟動 pipeline.
5. 檢查 `logs/telegram_listener.log` 是否有正確紀錄上述互動。

## 未來 AI 修改時的最低自我檢查標準

未來任何 AI 若修改了本專案，提交前至少要在回報中明確說明：

- 跑了哪些測試命令。
- 測試是否全部通過。
- 是否有手動驗證 Telegram listener。
- 若沒跑到某一步，原因是什麼。

## 常見偵錯與手動重跑指令 (Debug Records)

### 監看 Telegram Listener 即時日誌
```bash
tail -f logs/telegram_listener.log
```

### 處理 409 Conflict 或重複啟動問題
若 Telegram 回報 409 錯誤或發現訊息被重複處理，通常是多個實例在運行：
```bash
# 檢查並清理所有相關程序
ps aux | grep telegram_listener.py | grep -v grep | awk '{print $2}' | xargs kill -9

# 清理鎖定檔案
rm -f /tmp/telegram_listener.pid

# 重新啟動
python3 tools/telegram_listener.py
```

### 強制重新寄送今日摘要 (分開寄送、含網址)
若已產生摘要但想測試新格式/新收件人，先刪除已發送標記：
```bash
# 刪除所有已寄送標記
find output -name "*.mail-sent" -delete

# 模擬今日流程 (不重新轉錄，只做摘要與寄信)
source config/local_config.sh
python3 pipeline/run_daily_pipeline.py --debug --enable-transcribe 0 --concurrency 1 --enable-summarize 1 --traditionalize-transcript --date $(date +%Y-%m-%d)
```

### 檢查今日各頻道處理狀態
```bash
python3 tools/check_daily_status.py
```

### 驗證 Telegram 鎖定偵測 (Idle/Busy)
```bash
# 查看目前的鎖定描述
python3 -c "import sys; from pathlib import Path; sys.path.insert(0, 'tools'); from telegram_listener import TranscriptionStatusProvider; print(TranscriptionStatusProvider().describe())"

# 人為製造鎖定進行測試
touch /tmp/whisper_transcription.lock
# (檢查完後記得刪除)
rm /tmp/whisper_transcription.lock
```
