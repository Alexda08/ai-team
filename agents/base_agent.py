class BaseAgent:

    def __init__(self, name, system_prompt, llm, event_bus=None):
        self.name = name
        self.system_prompt = system_prompt
        self.llm = llm
        self._event_bus = event_bus

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

    def _emit_turn_start(self, input_summary=""):
        if self._event_bus:
            from core.events import EventType
            self._event_bus.publish(EventType.AGENT_TURN_START, {
                "agent": self.name,
                "input_summary": input_summary[:200],
            })

    def _emit_turn_end(self, output_summary="", **extra):
        if self._event_bus:
            from core.events import EventType
            self._event_bus.publish(EventType.AGENT_TURN_END, {
                "agent": self.name,
                "output_summary": output_summary[:200],
                **extra,
            })

    def run(self, messages):
        self._emit_turn_start()

        response = self.llm.generate(
            system=self.system_prompt,
            messages=self._prepare_messages(messages)
        )

        self._emit_turn_end(output_summary=response[:200] if response else "")

        return {
            "role": "agent",
            "agent": self.name,
            "content": response
        }
