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

            IMPORTANT:
            - The plan MUST be ordered by dependencies.
            - Each step MUST only depend on previous steps.
            - DO NOT include steps that require something not yet built.
            - If a step depends on another, it MUST appear AFTER it.

            Before finalizing, validate:
            - No circular dependencies
            - No missing prerequisites
            - Logical execution order.

            Output format (Markdown):

            # Project Plan

            ## Objective

            ## Architecture

            ## Modules

            ## Implementation Steps

            For each step include:
            - Step number
            - Title
            - Description
            - Dependencies (list of previous step numbers)

            Steps MUST be strictly ordered so that:
            - No step depends on a future step
            - The plan could be executed sequentially without blocking

            ## Risks

            ## Timeline

            Be detailed and structured.
            Do not debate. Just produce the final plan.
        """

        return self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}]
        )