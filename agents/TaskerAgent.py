from agents.base_agent import BaseAgent
import re, json

class TaskerAgent (BaseAgent):

    def __init__(self, name, system_prompt, llm):
        super().__init__(name, system_prompt, llm)

    def _is_task_too_big(self, task):
        description = task["description"].lower()
        keywords = [" and ", " then ", " after ", " before "]
        return any(k in description for k in keywords)

    def _count_actions(self, description):
        verbs = ["create", "validate", "save", "insert", "delete", "update", "generate"]
        return sum(1 for v in verbs if re.search(rf"\b{v}\b", description.lower()))

    def _validate_modularity(self, tasks):
        issues = []

        for task in tasks:
            if self._is_task_too_big(task):
                issues.append((task["id"], "too_many_steps"))

            if self._count_actions(task["description"]) > 1:
                issues.append((task["id"], "multiple_actions"))

        return issues

    def generate_tasks(self, plan):
        prompt = f"""
            INPUT:

            You are given a validated ACTION PLAN.

            Your task is to transform this plan into a structured list of executable tasks.

            ---

            ACTION PLAN:

            {plan}

            ---

            GOAL:

            Generate tasks that can be executed directly by an AI coding agent WITHOUT needing extra clarification.

            ---

            CORE RULES:

            - Each task MUST represent a SINGLE concrete action
            - Each task MUST be executable in isolation (given its dependencies)
            - Tasks must be small enough to be completed in one iteration

            ---

            EXECUTION-FOCUSED RULES (CRITICAL):

            - A task must produce a clear output (file, function, endpoint, test, etc.)
            - A task must NOT contain multiple outcomes
            - A task must NOT require interpretation

            ---

            SPLITTING RULES (VERY IMPORTANT):

            - If a task includes multiple endpoints → split into one task per endpoint
            - If a task includes setup + logic → split them
            - If a task includes multiple operations (create/update/delete) → split them
            - If a task includes validation + processing → split them
            - If a task includes DB + API logic → split them
            - If a task still requires multiple steps to complete, split it further
            - Each task should ideally map to a single function OR a single step inside a function
            - If a task description contains multiple verbs (validate, generate, save, insert), split it

            ---

            TASK DESCRIPTION RULES:

            Each description must:

            - Describe EXACTLY what to implement
            - Include expected behavior/output
            - Mention constraints if relevant
            - Be understandable without reading the full plan

            BAD:
            "Implement file system"

            GOOD:
            "Implement DiskStorage.save() that stores files in /uploads/{{year}}/{{month}}/{{uuid}}.{{ext}} and returns the relative path"
            ---

            DEPENDENCIES:

            - Only include dependencies if the task CANNOT run without them
            - Avoid unnecessary chaining
            - Prefer parallelizable tasks when possible

            ---

            MODEL ASSIGNMENT:

            - light → simple setup, config, small functions
            - medium → logic, DB, validation
            - heavy → only if truly unavoidable (try to avoid)

            ---

            OUTPUT REQUIREMENTS:

            - Return ONLY a valid JSON array
            - Do NOT include any text outside the JSON
            - Do NOT use markdown or ```json
            - Output must be directly parseable

            ---

            FINAL VALIDATION (MANDATORY):

            Before returning:

            - Check that no task contains multiple responsibilities
            - Check that each task has a clear output
            - Check that tasks are small enough for a single execution
            - If not, FIX them
        """

        return self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}]
        )

    def refine(self, tasks):
        try:
            tasks_parsed = json.loads(tasks)
        except json.JSONDecodeError:
            return "Invalid JSON in refine"

        issues = self._validate_modularity(tasks_parsed)

        if not issues:
            return tasks
        
        print("\nIssues found while refine:", issues)

        prompt = f"""
            You are given a list of tasks.

            Some tasks are too large or contain multiple responsibilities.

            current tasks:
            {tasks}
            ---
            Your job:

            - Split problematic tasks into smaller tasks
            - Preserve dependencies
            - Do NOT modify correct tasks

            Return full updated JSON.
        """

        return self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}]
        )    
        