import subprocess
import os
import json
import re
import time
import urllib.request
import urllib.error
import shutil
from pathlib import Path
from typing import List, Optional

# Use central logger
from tools.logger import get_logger
from tools.local_config import load_local_config
from tools.retry import retry
from tools.notifier import send_telegram_msg

logger = get_logger("summarizer")

_last_quota_alert_ts: float = 0
_QUOTA_ALERT_COOLDOWN = 3600

BASE_DIR = Path(__file__).resolve().parent.parent
GEMINI_API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


def _extract_retry_delay(error_body: str) -> float | None:
    match = re.search(r'retryDelay["\']?\s*:\s*["\']?(\d+(?:\.\d+)?)s', error_body)
    if match:
        return float(match.group(1))
    return None


def _alert_quota_exhausted(error: RuntimeError):
    global _last_quota_alert_ts
    now = time.time()
    if now - _last_quota_alert_ts < _QUOTA_ALERT_COOLDOWN:
        return
    _last_quota_alert_ts = now
    msg = "⚠️ Gemini API 三把 Key 配額皆已用完，目前改用 Ollama 摘要"
    logger.warning(msg, action="quota_exhausted_alert")
    send_telegram_msg(msg)


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
    text = re.sub(r"<br\s*/?>", "\n", text)
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
    def __init__(self, model: str | None = None):
        super().__init__("gemini")
        if not os.environ.get("GEMINI_API_KEY"):
            load_local_config(BASE_DIR / "config" / "local_config.sh", os.environ)
        primary_key = os.environ.get("GEMINI_API_KEY", "")
        fallback_key = os.environ.get("GEMINI_API_KEY_FALLBACK", "")
        fallback_key2 = os.environ.get("GEMINI_API_KEY_FALLBACK2", "")
        self.api_keys = [k for k in [primary_key, fallback_key, fallback_key2] if k]
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        self.fallback_models = [
            os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-2.0-flash-lite"),
            "gemini-flash-latest",
        ]
        self.timeout = int(os.environ.get("GEMINI_TIMEOUT_SECONDS", "300"))

    def is_available(self) -> bool:
        return bool(self.api_keys)

    def _call_api(self, model: str, api_key: str, full_prompt: str) -> Optional[str]:
        logger.info(f"Attempting Gemini API summary model={model}", action="summarize_start")
        self.last_model_used = model
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
            GEMINI_API_URL_TEMPLATE.format(model=model),
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-goog-api-key": api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                response = json.loads(res.read().decode("utf-8"))
            parts = response.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(part.get("text", "") for part in parts).strip()
            if not text:
                logger.error(f"Gemini API returned empty response model={model}", action="summarize_error")
                return None
            return text
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"Gemini API failed model={model} status={e.code} error=\"{error_body[:200]}\"", action="summarize_error")
            if e.code in RETRYABLE_HTTP_CODES:
                retry_delay = _extract_retry_delay(error_body)
                if retry_delay and e.code == 429:
                    logger.info(f"Waiting {retry_delay:.0f}s for quota reset model={model}", action="summarize_quota_wait")
                    time.sleep(retry_delay)
                raise RuntimeError(f"Gemini API 摘要失敗 ({e.code}): {error_body}")
            return None
        except Exception as e:
            logger.error(f"Gemini API exception model={model} error=\"{e}\"", action="summarize_error")
            raise RuntimeError(f"Gemini API 異常: {e}")

    @retry(max_retries=4, initial_delay=4, backoff_factor=4, max_delay=120, exceptions=RuntimeError)
    def summarize(self, full_prompt: str) -> Optional[str]:
        models_to_try = [self.model] + [m for m in self.fallback_models if m != self.model]
        last_error: Exception | None = None
        for key_i, api_key in enumerate(self.api_keys):
            for model_i, m in enumerate(models_to_try):
                if key_i > 0 or model_i > 0:
                    logger.info(f"Falling back to api_key={key_i} model={m}", action="summarize_fallback")
                try:
                    result = self._call_api(m, api_key, full_prompt)
                    if result is not None:
                        return result
                except RuntimeError as e:
                    last_error = e
                    continue
        if last_error:
            logger.warning(f"All Gemini API models/keys exhausted: {last_error}", action="summarize_all_exhausted")
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
        self.last_model_used = self.model
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

class OpenRouterSummarizer(BaseSummarizer):
    def __init__(self):
        super().__init__("openrouter")
        if not os.environ.get("OPENROUTER_API_KEY"):
            load_local_config(BASE_DIR / "config" / "local_config.sh", os.environ)
        self.api_key = os.environ.get("OPENROUTER_API_KEY", "")
        self.model = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
        self.timeout = int(os.environ.get("OPENROUTER_TIMEOUT_SECONDS", "300"))
        self.api_url = "https://openrouter.ai/api/v1/chat/completions"

    def is_available(self) -> bool:
        return bool(self.api_key)

    def summarize(self, full_prompt: str) -> Optional[str]:
        logger.info(f"Attempting OpenRouter summary model={self.model}", action="summarize_start")
        self.last_model_used = self.model
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": full_prompt}],
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.api_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                response = json.loads(res.read().decode("utf-8"))
            text = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if not text:
                logger.error(f"OpenRouter returned empty response model={self.model}", action="summarize_error")
                return None
            actual_model = response.get("model", self.model)
            self.last_model_used = actual_model
            return text
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")[:200]
            logger.error(f"OpenRouter API failed model={self.model} status={e.code} error=\"{error_body}\"", action="summarize_error")
            raise RuntimeError(f"OpenRouter 摘要失敗 ({e.code}): {error_body}")
        except Exception as e:
            logger.error(f"OpenRouter API exception model={self.model} error=\"{e}\"", action="summarize_error")
            raise RuntimeError(f"OpenRouter 異常: {e}")

def get_summarizers() -> List[BaseSummarizer]:
    all_classes = BaseSummarizer.__subclasses__()
    gemini_cls = next((c for c in all_classes if c.__name__ == "GeminiSummarizer"), None)
    ollama_cls = next((c for c in all_classes if c.__name__ == "OllamaSummarizer"), None)
    opencode_cls = next((c for c in all_classes if c.__name__ == "OpenCodeSummarizer"), None)
    openrouter_cls = next((c for c in all_classes if c.__name__ == "OpenRouterSummarizer"), None)
    summarizers = []
    if gemini_cls:
        summarizers.append(gemini_cls())
    if os.environ.get("ENABLE_OPENROUTER", "0") == "1" and openrouter_cls:
        summarizers.append(openrouter_cls())
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
            try:
                summary_text = summarizer.summarize(full_prompt)
            except RuntimeError as e:
                logger.error(f"Summarizer {summarizer.name} failed, trying next: {e}", action="summarize_fallback")
                if summarizer.name == "gemini" and "429" in str(e):
                    _alert_quota_exhausted(e)
                continue
            if summary_text:
                final_text = cleanup_latex(traditionalize_text(summary_text))
                model_tag = getattr(summarizer, 'last_model_used', summarizer.name)
                header = f"> 摘要模型：{model_tag}\n\n"
                output_md_path.write_text(header + final_text, encoding="utf-8")
                logger.info(f"Summary completed summarizer={summarizer.name} path={output_md_path.name}", action="summarize_ok")
                return output_md_path
    return None
