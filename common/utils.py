import os, json, questionary
from pathlib import Path
from questionary import Choice
from rich.console import Console

class Utils:
    @staticmethod
    def save_text(path, content):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
                return True, "success"
        except Exception as e:
            return False, f"No se pudo escribir '{path}': {e}"

    @staticmethod
    def load_text(filename: str, path: str) -> tuple[bool, str]:
        file_path = Path(path) / filename

        try:
            content = file_path.read_text(encoding="utf-8")
            return True, content
        except OSError as e:
            return False, f"No se pudo leer '{file_path}': {e}"

    @staticmethod
    def load_prompts() -> tuple[bool, dict]:
        location = Path("./agents/prompts")
        prompts = {}

        for prompt in location.iterdir():
            if prompt.is_file():
                success, content = Utils.load_text(prompt.name, location)
                if not success:
                    return False, content
                
                prompts[prompt.stem] = content
        
        return True, prompts

    @staticmethod
    def is_valid_json(data):
        try:
            if isinstance(data, str):
                json.loads(data)
            else:
                json.dumps(data)
            return True
        except (json.JSONDecodeError, TypeError):
            return False

    @staticmethod
    def select_menu(options={}, title="Select an option"):
        choice = questionary.select(
            title,
            choices=[
                Choice(value, value=key) for key, value in options.items()
            ]
        ).ask()

        return choice

    @staticmethod
    def console_print(text, color="white", bold=False, prefix=None):
        console = Console()
        style = color

        if bold:
            style = f"bold {color}"

        if prefix:
            text = f"{prefix} {text}"

        console.print(text, style=style)