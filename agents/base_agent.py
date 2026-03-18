class BaseAgent:

    def __init__(self, name, system_prompt, llm):
        self.name = name
        self.system_prompt = system_prompt
        self.llm = llm

    def run(self, messages):

        response = self.llm.generate(
            system=self.system_prompt,
            messages=messages
        )

        return {
            "role": "agent",
            "agent": self.name,
            "content": response
        }