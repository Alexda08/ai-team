import requests, os, json, traceback

PROVIDERS = {
    "ollama": {
        "host": "http://localhost:11434",
        "endpoint": "/api/chat",
        "format": "ollama"
    },
    "anthropic": {
        "host": "https://api.anthropic.com",
        "endpoint": "/v1/messages",
        "format": "anthropic"
    },
    "openai": {
        "host": "https://api.openai.com",
        "endpoint": "/v1/chat/completions",
        "format": "openai"
    }
}

class LLMClient:

    def __init__(self, model="minimax-m2.7:cloud", provider="ollama"):
        self.model = model
        self.provider = provider
        self.config = PROVIDERS[provider]

    @classmethod
    def from_config(cls, llm_config, model_override=None):
        """Create LLMClient from a LLMConfig dataclass.

        Args:
            llm_config: core.config.LLMConfig instance
            model_override: optional model name (overrides llm_config.default_model)
        """
        provider = llm_config.provider
        model = model_override or llm_config.default_model

        # Apply custom host if provided
        instance = cls(model=model, provider=provider)
        if llm_config.host and provider in PROVIDERS:
            instance.config = {**PROVIDERS[provider], "host": llm_config.host}
        return instance

    @classmethod
    def model_map_from_config(cls, llm_config):
        """Build the model_map dict (light/medium/heavy/ultra) from config."""
        return {
            tier: cls.from_config(llm_config, model_override=model_name)
            for tier, model_name in llm_config.model_map.items()
        }

    def generate(self, system: str, messages: list, json_schema: dict = None) -> str:
        try:
            if self.provider == "ollama":
                return self._call_ollama(system, messages, json_schema)
            elif self.provider == "anthropic":
                return self._call_anthropic(system, messages)
            elif self.provider == "openai":
                return self._call_openai(system, messages)
        except requests.exceptions.HTTPError as e:
            print(f"[LLMClient] HTTP error calling {self.provider}")
            print(f"Type: {type(e).__name__}")

            if e.response is not None:
                print(f"Status code: {e.response.status_code}")
                print(f"Response body: {_short(e.response.text, 3000)}")

            print(f"System: {_short(system)}")
            print("Messages summary:")
            for i, m in enumerate(messages):
                print(f"  [{i}] role={m.get('role')} content={_short(m.get('content'), 500)}")

            print("Traceback:")
            traceback.print_exc()
            raise

        except Exception as e:
            print(f"[LLMClient] Error calling {self.provider}: {e}")
            print(f"Type: {type(e).__name__}")
            print("Traceback:")
            traceback.print_exc()
            raise

    def _sanitize_messages(self, messages: list) -> list:
        role_map = {"agent": "assistant", "user": "user", "assistant": "assistant", "system": "system", "tool": "tool"}
        return [{"role": role_map.get(m["role"], "assistant"), "content": m["content"]} for m in messages]

    def _call_ollama(self, system: str, messages: list, json_schema: dict = None) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + self._sanitize_messages(messages),
            "stream": False,
            "options": {"temperature": 0.7} # this manages the creativity of the output 0.0 is deterministic, 1.0 is random
        }

        if json_schema:
            payload["format"] = json_schema

        response = requests.post(
            f"{self.config['host']}{self.config['endpoint']}",
            json=payload
        )
        response.raise_for_status()
        return response.json()["message"]["content"]

    def _call_anthropic(self, system: str, messages: list) -> str:
        headers = {
            "x-api-key": os.getenv("ANTHROPIC_API_KEY"),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        payload = {
            "model": self.model,
            "max_tokens": 8096,
            "system": system,
            "messages": messages
        }
        response = requests.post(
            f"{self.config['host']}{self.config['endpoint']}",
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        return response.json()["content"][0]["text"]

    def _call_openai(self, system: str, messages: list) -> str:
        headers = {
            "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages
        }
        response = requests.post(
            f"{self.config['host']}{self.config['endpoint']}",
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]