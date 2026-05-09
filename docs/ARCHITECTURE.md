# 專案架構與進階設定 (Architecture)

## 設計重點

- **模組化設計**: 核心邏輯位於 `pipeline/`，工具函式位於 `tools/`。
- **防止重複**: 每個收件人/通知管道都有獨立的 `mail-sent` 標記。
- **防休眠**: 使用 `caffeinate` 確保轉錄過程不中斷。
- **並行限制**: `gensrt.sh` 具備全域鎖，確保同一時間只有一個轉錄任務在運行。

## 執行模式

- **Release Mode**: 依照註冊檔寄送給所有收件人。
- **Debug Mode**: 使用 `--debug`，僅發送至開發者設定的測試地址。

## 跨平台部署

- **macOS**: 預設支援，可使用 `launchd` 排程。
- **Windows/Linux**: 
  - 不支援 Apple Mail，必須使用 SMTP。
  - 需要自行設定 `cron` 或工作排程器。
  - Windows 建議勾選「喚醒電腦以執行此工作」。

## 簡轉繁 (OpenCC)

若需開啟，請在 `local_config.sh` 設定：
```bash
OPENCC_TRADITIONALIZE="1"
OPENCC_CONFIG="s2twp.json"
```
這會自動產生 `.zh-Hant.txt` 檔案。
