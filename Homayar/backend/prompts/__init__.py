"""Prompt loader — reads prompt text files from the prompts directory."""
import os

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__))


def load_prompt(name: str) -> str:
    """Load a prompt file by name (e.g. 'qc_fa', 'problem_en')."""
    path = os.path.join(_PROMPTS_DIR, f"{name}.txt")
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()
