import subprocess
from pathlib import Path

DEFAULT_PROMPT = """這是一份由語音轉文字的財經節目逐字稿。由於是自動轉錄，可能包含許多同音異字的錯字，請在理解時運用你的財經知識自動修正這些錯字。

請根據以下提供的逐字稿內容進行總結。為避免幻覺，請「嚴格遵守」逐字稿中實際提及的內容，絕對不能捏造未提及的資訊、數據或外部新聞。

請以 Markdown 格式總結這集節目的核心重點，分條列出最關鍵的 3 到 5 個資訊。

以下為逐字稿內容：
-------------------
{transcript_content}
"""

def summarize_file(txt_path: Path, prompt_file: Path | None = None) -> Path | None:
    print(f"正在摘要: {txt_path.name} ...")
    
    transcript_content = txt_path.read_text(encoding="utf-8")
    
    if prompt_file and prompt_file.exists():
        template = prompt_file.read_text(encoding="utf-8")
    else:
        # Fallback to default if not configured or file missing
        default_path = Path("prompts/default.md")
        if default_path.exists():
            template = default_path.read_text(encoding="utf-8")
        else:
            template = DEFAULT_PROMPT

    full_prompt = template.replace("{transcript_content}", transcript_content)
    
    output_md_path = txt_path.with_name(f"{txt_path.stem}.summary.md")
    
    try:
        print(f"  嘗試使用 gemini-3-pro-preview 模型...")
        result = subprocess.run(
            ["gemini", "ask", "-m", "gemini-3-pro-preview", "請看我輸入的內容並進行摘要"], 
            input=full_prompt,
            text=True,
            capture_output=True,
            check=True
        )
        
        output_md_path.write_text(result.stdout, encoding="utf-8")
        print(f"✅ 摘要完成: {output_md_path.name}")
        return output_md_path
        
    except subprocess.CalledProcessError as e:
        print(f"⚠️ Pro 模型摘要失敗，嘗試退回使用 gemini-3-flash-preview 模型... (原因: {e.stderr.strip()})")
        try:
            result = subprocess.run(
                ["gemini", "ask", "--skip-trust", "-m", "gemini-3-flash-preview", "請看我輸入的內容並進行摘要"], 
                input=full_prompt,
                text=True,
                capture_output=True,
                check=True
            )
            
            output_md_path.write_text(result.stdout, encoding="utf-8")
            print(f"✅ 摘要完成 (使用 Flash 模型): {output_md_path.name}")
            return output_md_path
        except subprocess.CalledProcessError as e2:
            print(f"❌ 摘要失敗 (兩種模型皆失敗): {txt_path.name}")
            print(f"錯誤訊息: {e2.stderr}")
            return None
