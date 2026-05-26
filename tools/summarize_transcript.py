import subprocess
import os
import json
import urllib.request
import shutil
from pathlib import Path
from typing import List, Optional

# Use central logger
from tools.logger import get_logger

logger = get_logger("summarizer")

BASE_DIR = Path(__file__).resolve().parent.parent

def resolve_opencc_bin() -> str:
    """Resolve the opencc binary path with fallbacks."""
    env_bin = os.environ.get("OPENCC_BIN")
    if env_bin: return env_bin
    path_bin = shutil.which("opencc")
    if path_bin: return path_bin
    for p in ["/opt/homebrew/bin/opencc", "/usr/local/bin/opencc", "/usr/bin/opencc"]:
        if os.path.exists(p) and os.access(p, os.X_OK): return p
    return "opencc"

class BaseSummarizer:
    def __init__(self, name: str): self.name = name
    def is_available(self) -> bool: raise NotImplementedError
    def summarize(self, full_prompt: str) -> Optional[str]: raise NotImplementedError

class GeminiSummarizer(BaseSummarizer):
    def __init__(self): super().__init__("gemini")
    def is_available(self) -> bool:
        try:
            subprocess.run(["gemini", "--version"], capture_output=True, check=True)
            return True
        except: return False
    def summarize(self, full_prompt: str) -> Optional[str]:
        models = ["gemini-3-pro-preview", "gemini-3-flash-preview"]
        for model in models:
            try:
                logger.info(f"Attempting Gemini summary model={model}", action="summarize_start")
                res = subprocess.run(["gemini", "ask", "--skip-trust", "-m", model, "請看我輸入的內容並進行摘要"], input=full_prompt, text=True, capture_output=True, check=True)
                return res.stdout
            except subprocess.CalledProcessError as e:
                error_detail = e.stderr.strip()
                if model != models[-1] and ("429" in error_detail or "RESOURCE_EXHAUSTED" in error_detail or "capacity" in error_detail.lower()):
                    logger.warning(f"Gemini {model} failed (quota/capacity), falling back... error=\"{error_detail}\"", action="summarize_fallback")
                    continue
                logger.error(f"Gemini {model} failed error=\"{error_detail}\"", action="summarize_error")
                raise RuntimeError(f"Gemini {model} 摘要失敗: {error_detail}")
        return None

class OllamaSummarizer(BaseSummarizer):
    def __init__(self, model: str = "qwen2.5:7b"):
        super().__init__("ollama")
        self.model = model
    def is_available(self) -> bool:
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as response:
                data = json.loads(response.read().decode())
                models = [m["name"] for m in data.get("models", [])]
                return self.model in models or any(m.startswith(self.model) for m in models)
        except: return False
    def summarize(self, full_prompt: str) -> Optional[str]:
        logger.info(f"Attempting Ollama summary model={self.model}", action="summarize_start")
        try:
            data = json.dumps({"model": self.model, "prompt": full_prompt, "stream": False}).encode("utf-8")
            req = urllib.request.Request("http://localhost:11434/api/generate", data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as res:
                return json.loads(res.read().decode()).get("response")
        except Exception as e:
            logger.error(f"Ollama failed error=\"{e}\"", action="summarize_error")
            return None

def get_summarizers() -> List[BaseSummarizer]:
    all_classes = BaseSummarizer.__subclasses__()
    summarizers = []
    if os.environ.get("ENABLE_OLLAMA", "0") == "1":
        ollama_cls = next((c for c in all_classes if c.__name__ == "OllamaSummarizer"), None)
        if ollama_cls: summarizers.append(ollama_cls())
    for cls in all_classes:
        if cls.__name__ == "OllamaSummarizer": continue
        summarizers.append(cls())
    return summarizers

def traditionalize_text(text: str) -> str:
    config = os.environ.get("OPENCC_CONFIG", "s2twp.json")
    try:
        opencc_bin = resolve_opencc_bin()
        res = subprocess.run([opencc_bin, "-c", config], input=text, text=True, capture_output=True, check=True)
        return res.stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.info("OpenCC binary failed or not found, falling back to python implementation", action="traditionalize_fallback")
        try:
            from opencc import OpenCC
            config_name = config
            if config_name.endswith(".json"):
                config_name = config_name[:-5]
            converter = OpenCC(config_name)
            return converter.convert(text)
        except Exception as fe:
            logger.warning(f"OpenCC conversion (including fallback) failed error=\"{fe}\"", action="traditionalize_error")
            return text

def summarize_file(txt_path: Path, prompt_file: Path | None = None) -> Path | None:
    output_md_path = txt_path.with_name(f"{txt_path.stem}.summary.md")
    
    # Strictly use the provided prompt_file or the absolute default. No silent fallback.
    template_path = prompt_file if prompt_file else BASE_DIR / "prompts" / "default.md"
    
    if output_md_path.exists() and output_md_path.stat().st_mtime >= txt_path.stat().st_mtime:
        logger.info(f"Summary already exists and is up-to-date path={output_md_path.name}", action="summarize_skip")
        return output_md_path

    if not template_path.exists():
        raise FileNotFoundError(f"[CRITICAL] Prompt template missing: {template_path}")

    logger.info(f"Summarizing file path={txt_path.name} template={template_path.name}", action="summarize_file")
    transcript_content = txt_path.read_text(encoding="utf-8")
    template = template_path.read_text(encoding="utf-8")
    full_prompt = template.replace("{transcript_content}", transcript_content)

    for summarizer in get_summarizers():
        if summarizer.is_available():
            summary_text = summarizer.summarize(full_prompt)
            if summary_text:
                final_text = traditionalize_text(summary_text)
                output_md_path.write_text(final_text, encoding="utf-8")
                logger.info(f"Summary completed summarizer={summarizer.name} path={output_md_path.name}", action="summarize_ok")
                return output_md_path
    return None
