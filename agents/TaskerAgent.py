from agents.base_agent import BaseAgent

class TaskerAgent (BaseAgent):

    def __init__(self, name, system_prompt, llm):
        super().__init__(name, system_prompt, llm)

    def generate_tasks(self, plan):
        prompt = f"""INPUT:

            You are given the following validated ACTION PLAN.

            Your task is to transform this plan into a structured list of executable tasks.

            ---

            ACTION PLAN:

            {plan}

            ---

            INSTRUCTIONS:

            - Decompose the plan into atomic, executable tasks
            - Ensure tasks are clear, precise, and unambiguous
            - Maintain logical order
            - Define dependencies only when strictly necessary
            - Prefer parallelizable tasks when possible

            ---

            OUTPUT REQUIREMENTS:

            - Return ONLY a valid JSON array
            - Do NOT include any text outside the JSON
            - Do NOT explain your reasoning
            - Do NOT summarize the plan
            - Output must be parseable by a JSON parser

            ---

            REMINDER:

            - Each task must be minimal and executable
            - Avoid grouping multiple steps into a single task
            - Ensure the output is directly usable by an execution system
        """

        return self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}]
        )
        