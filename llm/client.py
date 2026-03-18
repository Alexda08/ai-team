import requests


class LLMClient:

    def __init__(self, model="claude", host="http://localhost:11434"):
        self.model = model
        self.host = host

    def generate(self, system: str, messages: list):

        prompt = self._format_messages(messages)

        response = requests.post(
            f"{self.host}/api/generate",
            json={
                "model": self.model,
                "system": system,
                "prompt": prompt,
                "stream": False
            }
        )

        data = response.json()

        return data["response"]

    def _format_messages(self, messages):

        text = ""

        for m in messages:
            role = m.get("role")
            content = m.get("content")

            text += f"{role.upper()}:\n{content}\n\n"

        return text