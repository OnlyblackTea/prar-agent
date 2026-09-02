"""从 llm/prompts/ 加载 prompt 模板文件（plan_engine 与 consolidator 共用）。"""
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(filename: str) -> str:
    target = (_PROMPTS_DIR / filename).resolve()
    if not target.is_relative_to(_PROMPTS_DIR):
        raise ValueError(f"Invalid prompt filename: {filename}")
    return target.read_text(encoding="utf-8")
