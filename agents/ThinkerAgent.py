from agents.base_agent import BaseAgent

class ThinkerAgent (BaseAgent):

    def __init__(self, name, system_prompt, llm):
        super().__init__(name, system_prompt, llm)

    def generate_plan(self, history):
        prompt = f"""
            You are THINKER.

            Based on the following full conversation, generate a detailed ACTION PLAN.

            Conversation:
            {history}

            Output format (Markdown):

            # Project Plan

            ## Objective

            ## Architecture

            ## Modules

            ## Implementation Steps

            ## Risks

            ## Timeline

            Be detailed and structured.
            Do not debate. Just produce the final plan.
        """

        return self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}]
        )