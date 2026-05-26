#!/usr/bin/env python3

import os
import sys
import subprocess
import argparse
from pathlib import Path
from datetime import datetime

# Setup paths
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "tools"))

from tools.logger import get_logger
from notifier import send_telegram_msg
from local_config import load_local_config

logger = get_logger("auto_fixer")

def get_relevant_logs(task_label: str, log_file: Path, max_lines: int = 50) -> str:
    """Extracts recent log lines matching the task label."""
    if not log_file.exists():
        return "Log file not found."
    
    try:
        # Use grep to find lines matching the task label
        cmd = ["grep", f"task=\"{task_label}\"", str(log_file)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        lines = result.stdout.strip().splitlines()
        return "\n".join(lines[-max_lines:]) if lines else "No matching log lines found."
    except Exception as e:
        return f"Error extracting logs: {e}"

def run_auto_fix(task_label: str, error_msg: str, log_file: str = None):
    logger.info(f"Starting auto-fix for task=\"{task_label}\"", action="fix_start")
    
    # 1. Gather Context
    readme = (BASE_DIR / "README.md").read_text(encoding="utf-8") if (BASE_DIR / "README.md").exists() else ""
    gemini_rules = (BASE_DIR / "GEMINI.md").read_text(encoding="utf-8") if (BASE_DIR / "GEMINI.md").exists() else ""
    
    log_path = Path(log_file) if log_file else (BASE_DIR / "launchd_download_and_transcribe.log")
    relevant_logs = get_relevant_logs(task_label, log_path)
    
    full_prompt = f"""
你是一位資深運維與開發專家。目前專案發生了任務失敗，請你分析並直接修復它。

### 專案背景 (README.md)
{readme}

### 開發規範 (GEMINI.md)
{gemini_rules}

### 失敗任務資訊
任務標籤: {task_label}
錯誤訊息: {error_msg}

### 相關日誌內容 (最新 {task_label} 記錄)
{relevant_logs}

### 你的任務
1. 找出失敗的 Root Cause。
2. 透過修改程式碼、設定檔或修復環境來解決問題（直接使用你的工具進行操作）。
3. 修復完成後，請整理一份簡短的報告回報給我。

### 回報格式 (必須包含以下標籤)
[🤖 自我修復報告]
[任務]：{task_label}
[Root Cause]：(說明原因)
[Solution]：(說明你做了什麼)
[狀態]：(成功/需要人工介入)
"""

    # 2. Invoke Gemini CLI in YOLO mode
    # We use a long timeout as fixing might take several turns
    try:
        cmd = [
            "/opt/homebrew/bin/gemini", 
            "--yolo", 
            "--skip-trust", 
            "--prompt", full_prompt
        ]
        
        logger.info("Invoking Gemini CLI for autonomous repair...", action="ai_invoke")
        # Note: In a real scenario, this might run for a while. 
        # We capture stdout to find the report tags.
        process = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(BASE_DIR))
        
        ai_output = process.stdout
        
        # 3. Extract and Send Report via Telegram
        report = ""
        if "[🤖 自我修復報告]" in ai_output:
            report = ai_output.split("[🤖 自我修復報告]")[1].strip()
            report = f"🤖 自我修復報告\n{report}"
        else:
            # Fallback if AI didn't follow format but produced output
            report = f"🤖 自我修復嘗試完成（任務：{task_label}）\n\nAI 輸出摘要：\n{ai_output[:500]}..."

        send_telegram_msg(report)
        logger.info(f"Auto-fix sequence completed for {task_label}", action="fix_done")

    except Exception as e:
        error_report = f"❌ 自我修復系統故障 (任務: {task_label})\n錯誤：{e}"
        logger.error(error_report, action="fix_error")
        send_telegram_msg(error_report)

if __name__ == "__main__":
    load_local_config(BASE_DIR / "config" / "local_config.sh", os.environ)
    
    parser = argparse.ArgumentParser(description="Autonomous Auto-Fixer")
    parser.add_argument("--task", required=True, help="Label of the failed task")
    parser.add_argument("--error", required=True, help="Error message encountered")
    parser.add_argument("--log", help="Path to the log file to analyze")
    
    args = parser.parse_args()
    run_auto_fix(args.task, args.error, args.log)
