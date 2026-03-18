import os

class Utils:
    @staticmethod
    def save_text(path, content):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)