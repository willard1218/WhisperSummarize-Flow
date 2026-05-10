import subprocess
import os
import json
import urllib.request
from pathlib import Path
from typing import List, Optional

DEFAULT_PROMPT_TEMPLATE = """這是一份由語音轉文字的財經節目逐字稿。由於是自動轉錄，可能包含許多同音異字的錯字，請在理解時運用你的財經知識自動修正這些錯字。

請根據以下提供的逐字稿內容進行總結。為避免幻覺，請「嚴格遵守」逐字稿中實際提及的內容，絕對不能捏造未提及的資訊、數據或外部新聞。

請以 Markdown 格式總結這集節目的核心重點，分條列出最關鍵的 3 到 5 個資訊。

以下為逐字稿內容：
-------------------
{transcript_content}
"""

class BaseSummarizer:
    def __init__(self, name: str):
        self.name = name

    def is_available(self) -> bool:
        raise NotImplementedError

    def summarize(self, full_prompt: str) -> Optional[str]:
        raise NotImplementedError

class GeminiSummarizer(BaseSummarizer):
    def __init__(self):
        super().__init__("gemini")

    def is_available(self) -> bool:
        try:
            subprocess.run(["gemini", "--version"], capture_output=True, check=True)
            return True
        except:
            return False

    def summarize(self, full_prompt: str) -> Optional[str]:
        try:
            print(f"  [Gemini] 嘗試使用 gemini-3-pro-preview 模型...")
            result = subprocess.run(
                ["gemini", "ask", "-m", "gemini-3-pro-preview", "請看我輸入的內容並進行摘要"], 
                input=full_prompt,
                text=True,
                capture_output=True,
                check=True
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            print(f"  [Gemini] Pro 模型摘要失敗，嘗試退回使用 gemini-3-flash-preview 模型... (原因: {e.stderr.strip()})")
            try:
                result = subprocess.run(
                    ["gemini", "ask", "--skip-trust", "-m", "gemini-3-flash-preview", "請看我輸入的內容並進行摘要"], 
                    input=full_prompt,
                    text=True,
                    capture_output=True,
                    check=True
                )
                return result.stdout
            except Exception:
                return None

class OllamaSummarizer(BaseSummarizer):
    def __init__(self, model: str = "qwen2.5:7b"):
        super().__init__("ollama")
        self.model = model

    def is_available(self) -> bool:
        try:
            url = "http://localhost:11434/api/tags"
            with urllib.request.urlopen(url, timeout=2) as response:
                data = json.loads(response.read().decode())
                models = [m["name"] for m in data.get("models", [])]
                return self.model in models or any(m.startswith(self.model) for m in models)
        except:
            return False

    def summarize(self, full_prompt: str) -> Optional[str]:
        print(f"  [Ollama] 嘗試使用 {self.model} 模型...")
        url = "http://localhost:11434/api/generate"
        payload = {"model": self.model, "prompt": full_prompt, "stream": False}
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode())
                return result.get("response")
        except Exception as e:
            print(f"  [Ollama] 摘要失敗: {e}")
            return None

def get_summarizers() -> List[BaseSummarizer]:
    """
    Dynamically discovers and returns instances of all available summarizers.
    Prioritizes Ollama if ENABLE_OLLAMA=1.
    """
    all_classes = BaseSummarizer.__subclasses__()
    summarizers = []
    
    # Priority handling: Ollama first if enabled
    if os.environ.get("ENABLE_OLLAMA", "0") == "1":
        ollama_cls = next((c for c in all_classes if c.__name__ == "OllamaSummarizer"), None)
        if ollama_cls:
            summarizers.append(ollama_cls())
    
    # Then add others (like Gemini)
    for cls in all_classes:
        if cls.__name__ == "OllamaSummarizer":
            continue
        summarizers.append(cls())
        
    return summarizers

def traditionalize_text(text: str) -> str:
    try:
        config = os.environ.get("OPENCC_CONFIG", "s2twp.json")
        result = subprocess.run(["opencc", "-c", config], input=text, text=True, capture_output=True, check=True)
        return result.stdout
    except Exception as e:
        print(f"  [OpenCC] 轉換摘要失敗: {e}")
        return text

def summarize_file(txt_path: Path, prompt_file: Path | None = None) -> Path | None:
    output_md_path = txt_path.with_name(f"{txt_path.stem}.summary.md")
    template_path = prompt_file if prompt_file and prompt_file.exists() else Path("prompts/default.md")
    
    if output_md_path.exists():
        if output_md_path.stat().st_mtime >= txt_path.stat().st_mtime:
            print(f"摘要已存在且為最新: {output_md_path.name}")
            return output_md_path

    print(f"正在摘要: {txt_path.name} ...")
    transcript_content = txt_path.read_text(encoding="utf-8")
    template = template_path.read_text(encoding="utf-8") if template_path.exists() else DEFAULT_PROMPT_TEMPLATE
    full_prompt = template.replace("{transcript_content}", transcript_content)

    for summarizer in get_summarizers():
        if summarizer.is_available():
            summary_text = summarizer.summarize(full_prompt)
            if summary_text:
                final_text = traditionalize_text(summary_text)
                output_md_path.write_text(final_text, encoding="utf-8")
                print(f"[OK] 摘要完成 ({summarizer.name}): {output_md_path.name}")
                return output_md_path

    return None
