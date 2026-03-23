class BaseAgent:

    def __init__(self, name, system_prompt, llm):
        self.name = name
        self.system_prompt = system_prompt
        self.llm = llm

    def _prepare_messages(self, messages):
        """Convert shared history to this agent's perspective.
        Own messages -> assistant, others (agents/user) -> user.
        Ensures proper user/assistant alternation for the LLM."""
        result = []
        for m in messages:
            role = m.get("role", "user")
            if role == "agent":
                if m.get("agent") == self.name:
                    result.append({"role": "assistant", "content": m["content"]})
                else:
                    result.append({"role": "user", "content": m["content"]})
            else:
                result.append({"role": "user", "content": m["content"]})
        return result

    def run(self, messages):

        response = self.llm.generate(
            system=self.system_prompt,
            messages=self._prepare_messages(messages)
        )

        return {
            "role": "agent",
            "agent": self.name,
            "content": response
        }