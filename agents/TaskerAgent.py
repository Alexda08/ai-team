from agents.base_agent import BaseAgent
import re, json

TASK_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "type": {
                "type": "string",
                "enum": ["frontend", "backend", "infra", "integration", "general"]
            },
            "model": {
                "type": "string",
                "enum": ["light", "medium", "heavy"]
            },
            "dependencies": {
                "type": "array",
                "items": {"type": "integer"}
            }
        },
        "required": ["id", "title", "description", "type", "model", "dependencies"]
    }
}

class TaskerAgent (BaseAgent):

    def __init__(self, name, system_prompt, llm):
        super().__init__(name, system_prompt, llm)

    def _is_task_too_big(self, task):
        title = task["title"].lower()
        description = task["description"].lower()

        title_patterns = [" and create ", " and implement ", " and configure ", " and set up ", " and add ", " then "]
        if any(p in title for p in title_patterns):
            return True

        if self._count_actions(description) > 2:  # antes era > 1
            return True

        if len(description) > 400:
            return True

        # Múltiples endpoints en una tarea
        endpoints = re.findall(r'(GET|POST|PUT|DELETE|PATCH)\s+/', task["description"])
        if len(endpoints) > 1:
            return True

        return False

    def _count_actions(self, description):
        verbs = ["create", "validate", "save", "insert", "delete", "update", "generate"]
        return sum(1 for v in verbs if re.search(rf"\b{v}\b", description.lower()))

    def _validate_modularity(self, tasks):
        issues = []
        seen = set()

        for task in tasks:
            task_id = task["id"]
            if task_id in seen:
                continue

            if self._is_task_too_big(task):
                issues.append((task_id, "too_many_steps"))
                seen.add(task_id)

        return issues


    def _call_refine(self, tasks, issues):
        problem_ids = {issue[0] for issue in issues}
        problem_tasks = [t for t in tasks if t["id"] in problem_ids]
        ok_tasks = [t for t in tasks if t["id"] not in problem_ids]

        print("\nIssues found while refine:", issues)
        print("Splitting tasks:", [t["id"] for t in problem_tasks])

        prompt = f"""
            You are given a list of tasks that are too large or contain multiple responsibilities.

            YOUR ONLY JOB: Split each task into 2 or more smaller atomic tasks.

            TASKS TO SPLIT:
            {problem_tasks}
            ---

            RULES:
            - Each output task must have a SINGLE responsibility
            - Preserve dependencies from the original task
            - Do NOT include tasks that are not in the input list
            - Return ONLY a valid JSON array of the new split tasks
            - Do NOT wrap in markdown
        """

        raw = self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
            json_schema=TASK_SCHEMA
        )

        new_tasks = json.loads(raw)

        # Reasignar IDs a las nuevas tareas
        max_id = max(t["id"] for t in tasks)
        for i, t in enumerate(new_tasks):
            t["id"] = max_id + i + 1

        # Construir mapping: old_problem_id → lista de new_ids que heredan sus deps
        # Agrupamos las nuevas tareas por sus dependencias originales
        # La clave: las nuevas tareas heredan las deps de la tarea original
        # así que cualquier referencia a problem_id debe apuntar a TODAS las nuevas
        new_ids = [t["id"] for t in new_tasks]

        # Actualizar dependencias en ok_tasks
        for task in ok_tasks:
            new_deps = []
            changed = False
            for dep in task["dependencies"]:
                if dep in problem_ids:
                    # Reemplazar con todos los nuevos IDs
                    new_deps.extend(new_ids)
                    changed = True
                else:
                    new_deps.append(dep)
            if changed:
                task["dependencies"] = list(set(new_deps))

        return ok_tasks + new_tasks

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

            TYPE ASSIGNMENT:

            Assign the most accurate type based on what the task actually does:

            - "backend" → Python logic, database queries, API endpoints, services, repositories, schemas
            - "infra" → Docker, requirements.txt, alembic configuration, project directory structure, CI/CD, environment setup
            - "integration" → connects two systems (cache + DB, email + service, background jobs)
            - "general" → anything that doesn't clearly fit the above
            - "frontend" → UI components, CSS, client-side JS (unlikely in backend projects)

            IMPORTANT: Do NOT default everything to "backend". Infra and integration tasks must be correctly typed.

            ---

            MODEL ASSIGNMENT:

            Assign complexity honestly based on what the task requires to implement:

            - "light" → config files, simple schemas, basic utility functions, directory creation, small CRUD helpers
            - "medium" → business logic, DB queries with filters, API endpoints, validation logic, pagination
            - "heavy" → complex systems that require deep reasoning:
                - Authentication with token rotation and reuse detection
                - Cache systems with versioned invalidation
                - Multi-step atomic transactions
                - Permission and RBAC systems
                - Background job orchestration
                - Soft-delete with cascade operations
                - Repository methods with window functions

            IMPORTANT: Use "heavy" whenever a task requires designing non-trivial logic, not just writing boilerplate.

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
            - Check that types are correctly assigned (not everything is "backend")
            - Check that heavy tasks are properly identified
            - If any of the above fails, FIX it before returning
        """

        return self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
            json_schema=TASK_SCHEMA
        )
    
    # actually this isn't used
    def generate_phases(self, plan):
        prompt = f"""
            INPUT:

            You are given a validated ACTION PLAN.

            Your task is NOT to create tasks.

            Your task is to organize the plan into EXECUTION PHASES based on dependencies.

            ---

            ACTION PLAN:

            {plan}

            ---

            GOAL:

            Identify a sequence of phases that represent the correct execution order of the system.

            Each phase must group work that can be executed at the same stage of the system.

            ---

            CORE RULES:

            - Phases MUST be ordered by dependency
            - A phase can ONLY depend on previous phases
            - NO phase can depend on a future phase
            - Each phase must be independently executable once its dependencies are completed

            ---

            IMPORTANT:

            Phases are NOT categories like "frontend", "backend", "database"

            Phases MUST represent dependency layers such as:

            - infrastructure
            - database schema
            - core entities
            - business logic
            - API layer
            - AI / advanced systems

            ---

            PHASE DESIGN RULES:

            - Each phase must have a clear purpose
            - Each phase must represent a logical step in system construction
            - Avoid too many small phases
            - Avoid mixing unrelated concerns in the same phase
            - Prefer grouping by dependency, not by technology

            ---
            PHASE DEFINITION RULE:
            - A phase MUST represent a dependency layer, NOT a feature group.
            - If a phase contains multiple independent responsibilities, it MUST be split.
            - A phase must be the MINIMUM set of work required before the next phase can start.
            ---

            OUTPUT STRUCTURE:

            Return ONLY valid JSON.

            Do NOT include explanations.

            Format:

            {{
                "phases": [
                    {{
                    "id": integer,
                    "name": string,
                    "description": string,
                    "dependencies": [phase_ids]
                    }}
                ]
            }}

            ---

            VALIDATION (MANDATORY):

            Before returning:

            - Ensure phases are in correct execution order
            - Ensure no circular dependencies
            - Ensure each phase only depends on previous phases
            - Ensure phases reflect real build order (not arbitrary grouping)

            ---

            CRITICAL:

            - Do NOT generate tasks
            - Do NOT include implementation details
            - Do NOT explain anything
            - Output ONLY JSON
        """

        return self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}]
        )


    def refine(self, tasks, max_attempts=5):
        try:
            tasks_parsed = json.loads(tasks)
            issues = self._validate_modularity(tasks_parsed)
            attempts = 0

            while issues and attempts < max_attempts:
                tasks_parsed = self._call_refine(tasks_parsed, issues)
                issues = self._validate_modularity(tasks_parsed)
                print(f"Phase {attempts} of refine.")
                attempts += 1

            if attempts >= max_attempts and issues:
                print("\nMax attempts reached while refine.")
            else:
                print("\nNo more issues found while refine.")

            return tasks_parsed
        except json.JSONDecodeError:
            print("Invalid JSON in refine")
            return []