import os
from pathlib import Path

class Utils:
    @staticmethod
    def save_text(path, content):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    
    # add try except for file not found
    @staticmethod
    def load_text(filename: str, path: str = "prompts") -> tuple[bool, str]:
        file_path = Path(path) / filename

        try:
            content = file_path.read_text(encoding="utf-8")
            return True, content
        except OSError as e:
            return False, f"No se pudo leer '{file_path}': {e}"