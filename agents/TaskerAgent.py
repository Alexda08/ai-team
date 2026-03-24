from agents.base_agent import BaseAgent
from common.utils import Utils
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

class TaskerAgent(BaseAgent):

    def __init__(self, name, system_prompt, llm):
        super().__init__(name, system_prompt, llm)

    # --- Validation ---

    def _is_task_too_big(self, task):
        title = task["title"].lower()
        description = task["description"].lower()

        title_patterns = [" and create ", " and implement ", " and configure ", " and set up ", " and add ", " then "]
        if any(p in title for p in title_patterns):
            return True

        if self._count_actions(description) > 2:
            return True

        if len(description) > 400:
            return True

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

    def _validate_dependencies(self, tasks):
        issues = []
        task_ids = {t["id"] for t in tasks}
        task_map = {t["id"]: t for t in tasks}

        # Build specific identifier -> task_id mapping
        # Only match class names, function names, module names — not generic verbs
        identifier_to_task = {}
        for t in tasks:
            # Extract PascalCase identifiers (class names like MessageBus, LLMClient)
            classes = re.findall(r'\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b', t["title"])
            # Extract snake_case identifiers (function names like get_relevant_context)
            functions = re.findall(r'\b([a-z]+_[a-z_]+)\b', t["title"])
            # Extract specific filenames
            files = re.findall(r'[\w/]+\.(?:py|js|ts|yaml|json)\b', t["description"])
            
            for identifier in classes + functions + files:
                identifier_lower = identifier.lower()
                # Skip overly generic terms
                if identifier_lower in {"create", "implement", "test", "write", "add", "update"}:
                    continue
                identifier_to_task.setdefault(identifier_lower, []).append(t["id"])

        for task in tasks:
            desc_lower = task["description"].lower()
            title_lower = task["title"].lower()
            current_deps = set(task["dependencies"])

            for identifier, source_ids in identifier_to_task.items():
                # Check if description references this identifier
                if identifier in desc_lower or identifier in title_lower:
                    for source_id in source_ids:
                        if source_id != task["id"] and source_id not in current_deps:
                            if source_id < task["id"]:
                                issues.append((task["id"], "missing_dependency", source_id))

            # Validate existing deps
            for dep in task["dependencies"]:
                if dep not in task_ids:
                    issues.append((task["id"], "invalid_dependency", dep))
                if dep == task["id"]:
                    issues.append((task["id"], "self_dependency", dep))

        # Global check — but raise threshold
        if len(tasks) > 10:
            empty_deps = sum(1 for t in tasks if not t["dependencies"])
            if empty_deps / len(tasks) > 0.7:
                issues.append(("global", "too_many_independent_tasks", f"{empty_deps}/{len(tasks)}"))

        return issues

    def _has_dependency_cycle(self, tasks):
        """Detect cycles in dependency graph using DFS."""
        task_map = {t["id"]: t for t in tasks}
        visited = set()
        rec_stack = set()

        def dfs(task_id):
            visited.add(task_id)
            rec_stack.add(task_id)
            task = task_map.get(task_id)
            if task:
                for dep in task["dependencies"]:
                    if dep not in visited:
                        if dfs(dep):
                            return True
                    elif dep in rec_stack:
                        return True
            rec_stack.discard(task_id)
            return False

        for task in tasks:
            if task["id"] not in visited:
                if dfs(task["id"]):
                    return True
        return False

    # --- Generation ---

    def generate_tasks(self, plan):
        prompt = f"""ACTION PLAN TO DECOMPOSE:
            {plan}
            Transform this plan into executable tasks. Return ONLY a valid JSON array.
        """

        raw = self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
            json_schema=TASK_SCHEMA
        )

        return Utils.clean_json(raw)

    # --- Refinement ---

    def _call_refine(self, tasks, issues):
        problem_ids = {issue[0] for issue in issues if issue[0] != "global"}
        problem_tasks = [t for t in tasks if t["id"] in problem_ids]
        ok_tasks = [t for t in tasks if t["id"] not in problem_ids]

        print(f"\nIssues found: {issues}")
        print(f"Tasks to fix: {[t['id'] for t in problem_tasks]}")

        prompt = f"""You are given tasks that need to be fixed.

            TASKS WITH ISSUES:
            {json.dumps(problem_tasks, indent=2)}

            ISSUES FOUND:
            {json.dumps([(str(i[0]), i[1], str(i[2]) if len(i) > 2 else "") for i in issues if i[0] != "global"], indent=2)}

            ALL EXISTING TASK IDS (for dependency reference):
            {json.dumps([t["id"] for t in ok_tasks])}

            FIX RULES:
            - For "too_many_steps": split into 2+ smaller atomic tasks
            - For "missing_dependency": add the missing dependency id to the dependencies array
            - For "invalid_dependency": remove or replace the invalid dependency
            - Preserve valid dependencies from the original task
            - Return ONLY a valid JSON array of the fixed tasks
            - Do NOT include unchanged tasks
            - Do NOT wrap in markdown
        """

        raw = self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
            json_schema=TASK_SCHEMA
        )

        new_tasks = json.loads(raw)

        # Reassign IDs only for split tasks (new tasks)
        existing_ids = {t["id"] for t in ok_tasks}
        max_id = max(t["id"] for t in tasks)

        for t in new_tasks:
            if t["id"] in existing_ids or t["id"] in problem_ids:
                # This is a replacement — keep the ID if it was a problem task being fixed
                if t["id"] not in problem_ids:
                    max_id += 1
                    t["id"] = max_id
            # Validate deps exist
            t["dependencies"] = [d for d in t["dependencies"] if d in existing_ids or d in {nt["id"] for nt in new_tasks}]

        return ok_tasks + new_tasks

    def refine(self, tasks, max_attempts=5):
        try:
            tasks_parsed = json.loads(tasks) if isinstance(tasks, str) else tasks
            attempts = 0

            while attempts < max_attempts:
                # Check modularity
                modularity_issues = self._validate_modularity(tasks_parsed)

                # Check dependencies
                dep_issues = self._validate_dependencies(tasks_parsed)

                # Check cycles
                if self._has_dependency_cycle(tasks_parsed):
                    dep_issues.append(("global", "circular_dependency_detected", ""))

                all_issues = modularity_issues + dep_issues

                if not all_issues:
                    print(f"\nAll validations passed after {attempts} refinement(s).")
                    break

                print(f"\nRefinement round {attempts + 1}: {len(all_issues)} issues found")
                tasks_parsed = self._call_refine(tasks_parsed, all_issues)
                attempts += 1

            if attempts >= max_attempts:
                print(f"\nMax attempts ({max_attempts}) reached. Remaining issues may exist.")

            return tasks_parsed

        except json.JSONDecodeError as e:
            print(f"Invalid JSON in refine: {e}")
            return []