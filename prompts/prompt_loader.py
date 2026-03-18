from pathlib import Path

def load_prompt(filename: str) -> str:
    path = Path("prompts") / filename
    with open(path, "r", encoding="utf-8") as f:
        return f.read()