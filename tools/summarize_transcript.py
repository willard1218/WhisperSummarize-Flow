import subprocess
import os
import json
import urllib.request
import urllib.error
import shutil
from pathlib import Path
from typing import List, Optional

# Use central logger
from tools.logger import get_logger
from tools.local_config import load_local_config
from tools.retry import retry

logger = get_logger("summarizer")

BASE_DIR = Path(__file__).resolve().parent.parent
GEMINI_API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}

LATEX_REPLACEMENTS = [
    (r"\\rightarrow", "→"),
    (r"\\Rightarrow", "⇒"),
    (r"\\leftarrow", "←"),
    (r"\\Leftarrow", "⇐"),
    (r"\\leftrightarrow", "↔"),
    (r"\\Leftrightarrow", "⇔"),
    (r"\\to", "→"),
    (r"\\mapsto", "→"),
    (r"\\times", "×"),
    (r"\\cdot", "·"),
    (r"\\div", "÷"),
    (r"\\pm", "±"),
    (r"\\ge", "≥"),
    (r"\\geq", "≥"),
    (r"\\le", "≤"),
    (r"\\leq", "≤"),
    (r"\\ne", "≠"),
    (r"\\neq", "≠"),
    (r"\\approx", "≈"),
    (r"\\propto", "∝"),
    (r"\\infty", "∞"),
    (r"\\sum", "Σ"),
    (r"\\prod", "Π"),
    (r"\\int", "∫"),
    (r"\\partial", "∂"),
    (r"\\Delta", "Δ"),
    (r"\\Gamma", "Γ"),
    (r"\\Theta", "Θ"),
    (r"\\Lambda", "Λ"),
    (r"\\Omega", "Ω"),
    (r"\\alpha", "α"),
    (r"\\beta", "β"),
    (r"\\gamma", "γ"),
    (r"\\delta", "δ"),
    (r"\\epsilon", "ε"),
    (r"\\zeta", "ζ"),
    (r"\\eta", "η"),
    (r"\\theta", "θ"),
    (r"\\iota", "ι"),
    (r"\\kappa", "κ"),
    (r"\\lambda", "λ"),
    (r"\\mu", "μ"),
    (r"\\nu", "ν"),
    (r"\\xi", "ξ"),
    (r"\\pi", "π"),
    (r"\\rho", "ρ"),
    (r"\\sigma", "σ"),
    (r"\\tau", "τ"),
    (r"\\upsilon", "υ"),
    (r"\\phi", "φ"),
    (r"\\chi", "χ"),
    (r"\\psi", "ψ"),
    (r"\\omega", "ω"),
    (r"\$", ""),
]

def cleanup_latex(text: str) -> str:
    import re
    for pattern, replacement in LATEX_REPLACEMENTS:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"\$\$", "", text)
    text = re.sub(r"\\frac\{([^}]*)\}\{([^}]*)\}", r"\1/\2", text)
    text = re.sub(r"\\sqrt(?:\[([^}]*)\])?\{([^}]*)\}", r"√(\2)", text)
    text = re.sub(r"\\text\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\textbf\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\textit\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\displaystyle\s*", "", text)
    text = re.sub(r"\\limits\s*", "", text)
    text = re.sub(r"\\(?:left|right)[\\()\\[\\]{}|]", "", text)
    return text.strip()

def resolve_opencode_bin() -> str:
    env_bin = os.environ.get("OPENCODE_BIN")
    if env_bin: return env_bin
    path_bin = shutil.which("opencode")
    return path_bin if path_bin else "opencode"

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
    def __init__(self):
        super().__init__("gemini")
        if not os.environ.get("GEMINI_API_KEY"):
            load_local_config(BASE_DIR / "config" / "local_config.sh", os.environ)
        self.api_key = os.environ.get("GEMINI_API_KEY", "")
        self.model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
        self.timeout = int(os.environ.get("GEMINI_TIMEOUT_SECONDS", "300"))

    def is_available(self) -> bool:
        return bool(self.api_key)

    @retry(max_retries=3, initial_delay=2, backoff_factor=3, exceptions=RuntimeError)
    def summarize(self, full_prompt: str) -> Optional[str]:
        logger.info(f"Attempting Gemini API summary model={self.model}", action="summarize_start")
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": full_prompt}
                    ]
                }
            ]
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            GEMINI_API_URL_TEMPLATE.format(model=self.model),
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-goog-api-key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                response = json.loads(res.read().decode("utf-8"))
            parts = response.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(part.get("text", "") for part in parts).strip()
            if not text:
                logger.error("Gemini API returned empty response", action="summarize_error")
                return None
            return text
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"Gemini API failed status={e.code} error=\"{error_body}\"", action="summarize_error")
            if e.code in RETRYABLE_HTTP_CODES:
                raise RuntimeError(f"Gemini API 摘要失敗 ({e.code}): {error_body}")
            return None
        except Exception as e:
            logger.error(f"Gemini API exception error=\"{e}\"", action="summarize_error")
            return None

class OpenCodeSummarizer(BaseSummarizer):
    def __init__(self): super().__init__("opencode")
    def is_available(self) -> bool:
        try:
            subprocess.run([resolve_opencode_bin(), "--version"], capture_output=True, check=True)
            return True
        except: return False
    def summarize(self, full_prompt: str) -> Optional[str]:
        import tempfile, os
        tmp = None
        try:
            tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
            tmp.write(full_prompt)
            tmp_path = tmp.name
            tmp.close()

            opencode_bin = resolve_opencode_bin()
            logger.info("Attempting OpenCode summary", action="summarize_start")
            res = subprocess.run(
                [opencode_bin, "run", "-m", "opencode/big-pickle",
                 "--dangerously-skip-permissions", "--file", tmp_path,
                 "Summarize the following transcript according to the instructions in the attached file."],
                text=True, capture_output=True, check=True, timeout=300
            )
            return res.stdout
        except subprocess.CalledProcessError as e:
            error_detail = e.stderr.strip()
            logger.error(f"OpenCode failed error=\"{error_detail}\"", action="summarize_error")
            raise RuntimeError(f"OpenCode 摘要失敗: {error_detail}")
        except subprocess.TimeoutExpired:
            logger.error("OpenCode summary timed out", action="summarize_error")
            return None
        except Exception as e:
            logger.error(f"OpenCode summary exception error=\"{e}\"", action="summarize_error")
            return None
        finally:
            if tmp:
                try: os.unlink(tmp_path)
                except: pass

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
            with urllib.request.urlopen(req, timeout=180) as res:
                response = json.loads(res.read().decode()).get("response")
            if not response or not response.strip():
                logger.error(f"Ollama returned empty response for model={self.model}", action="summarize_error")
                return None
            return response
        except Exception as e:
            logger.error(f"Ollama failed error=\"{e}\"", action="summarize_error")
            return None

def get_summarizers() -> List[BaseSummarizer]:
    all_classes = BaseSummarizer.__subclasses__()
    gemini_cls = next((c for c in all_classes if c.__name__ == "GeminiSummarizer"), None)
    ollama_cls = next((c for c in all_classes if c.__name__ == "OllamaSummarizer"), None)
    opencode_cls = next((c for c in all_classes if c.__name__ == "OpenCodeSummarizer"), None)
    summarizers = []
    if gemini_cls:
        summarizers.append(gemini_cls())
    if os.environ.get("ENABLE_OLLAMA", "0") == "1" and ollama_cls:
        summarizers.append(ollama_cls())
    if os.environ.get("ENABLE_OPENCODE", "0") == "1" and opencode_cls:
        summarizers.append(opencode_cls())
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
                final_text = cleanup_latex(traditionalize_text(summary_text))
                output_md_path.write_text(final_text, encoding="utf-8")
                logger.info(f"Summary completed summarizer={summarizer.name} path={output_md_path.name}", action="summarize_ok")
                return output_md_path
    return None
