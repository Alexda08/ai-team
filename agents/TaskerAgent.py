from agents.base_agent import BaseAgent
from common.utils import Utils
import json

TASK_TREE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "title": {"type": "string"},
            "file": {"type": "string"},
            "depends_on": {
                "type": "array",
                "items": {"type": "integer"}
            },
            "subtasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "model": {
                            "type": "string",
                            "enum": ["light", "medium", "heavy"]
                        },
                        "phase": {
                            "type": "string",
                            "enum": ["build", "wire"]
                        }
                    },
                    "required": ["id", "title", "description", "model"]
                }
            }
        },
        "required": ["id", "title", "file", "depends_on", "subtasks"]
    }
}

class TaskerAgent(BaseAgent):

    def __init__(self, name, system_prompt, llm):
        super().__init__(name, system_prompt, llm)

    # --- Generation ---

    def generate_tasks(self, blueprint, plan=None):
        if plan:
            prompt = f"""
                PROJECT BLUEPRINT:
                {blueprint}
                
                ACTION PLAN:
                {plan}
                
                Transform the Blueprint and Plan into a tree of executable task groups.
                Each group maps to ONE file from the Blueprint. Each subtask is an atomic coding unit.
                
                REMEMBER:
                - Copy file paths EXACTLY from the Blueprint
                - Copy depends_on from the Blueprint's DEPENDENCY ORDER
                - Every subtask description must start with "In {{file_path}}:"
                - Include method signatures from the Blueprint in descriptions
                - Include business context from the Plan in descriptions
                - Include the export statement from the Blueprint in build task descriptions
                - Do NOT create groups for empty __init__.py files
                - Every subtask MUST have a "phase" field: "build" or "wire"
                - Entry point files and IPC/API bridge files MUST have a wire task as last subtask
                - Files with 3+ imports SHOULD have a wire task
                - If Blueprint has CONVENTIONS, reference export/import style in wire tasks
                - If Blueprint has INTEGRATION CONTRACTS, reference them in wire tasks  
                - If Blueprint has CONSTRUCTOR REGISTRY, include it in entry point wire tasks
                
                Return ONLY a valid JSON array of groups.
            """
        else:
            prompt = f"""
                PROJECT BLUEPRINT:
                {blueprint}
                
                Transform this Blueprint into a tree of executable task groups.
                Each group maps to ONE file from the Blueprint. Each subtask is an atomic coding unit.
                
                REMEMBER:
                - Copy file paths EXACTLY from the Blueprint
                - Copy depends_on from the Blueprint's DEPENDENCY ORDER
                - Every subtask description must start with "In {{file_path}}:"
                - Include method signatures from the Blueprint in descriptions
                - Use the Blueprint's purpose and notes fields for context in descriptions
                - Include the export statement from the Blueprint in build task descriptions
                - Do NOT create groups for empty __init__.py files
                - Every subtask MUST have a "phase" field: "build" or "wire"
                - Entry point files and IPC/API bridge files MUST have a wire task
                
                Return ONLY a valid JSON array of groups.
            """
 
        raw = self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
            json_schema=TASK_TREE_SCHEMA
        )
 
        return Utils.clean_json(raw)

    # --- Validation ---

    def validate(self, task_tree):
        try:
            groups = json.loads(task_tree) if isinstance(task_tree, str) else task_tree
        except json.JSONDecodeError as e:
            return None, [f"Invalid JSON: {e}"]

        issues = []
        group_ids = {g["id"] for g in groups}

        for group in groups:
            gid = group["id"]

            # Check depends_on references exist
            for dep in group.get("depends_on", []):
                if dep not in group_ids:
                    issues.append(f"Group {gid}: depends_on group {dep} which does not exist")
                if dep == gid:
                    issues.append(f"Group {gid}: self-dependency")

            # Check file is present
            if not group.get("file"):
                issues.append(f"Group {gid}: missing file path")

            # Check subtasks exist
            subtasks = group.get("subtasks", [])
            if not subtasks:
                issues.append(f"Group {gid}: has no subtasks")

            has_wire = False
            build_after_wire = False

            for st in subtasks:
                sid = st.get("id", "?")

                # Check subtask id format
                if not isinstance(sid, str) or "." not in sid:
                    issues.append(f"Subtask {sid}: id must be string format 'group.number' (e.g., '{gid}.1')")

                # Check description starts with file path
                desc = st.get("description", "")
                if not desc.startswith("In "):
                    issues.append(f"Subtask {sid}: description must start with 'In {{file_path}}:'")

                # Check description is not too vague
                if len(desc) < 50:
                    issues.append(f"Subtask {sid}: description too short ({len(desc)} chars), needs more detail")

                # Check model is valid
                if st.get("model") not in ("light", "medium", "heavy"):
                    issues.append(f"Subtask {sid}: invalid model '{st.get('model')}'")

                # Default phase to "build" if missing
                if "phase" not in st:
                    st["phase"] = "build"

                # Check phase is valid
                if st.get("phase") not in ("build", "wire"):
                    issues.append(f"Subtask {sid}: invalid phase '{st.get('phase')}'")

                # Check build tasks don't come after wire tasks
                if st.get("phase") == "wire":
                    has_wire = True
                elif has_wire:
                    build_after_wire = True

            if build_after_wire:
                issues.append(f"Group {gid}: build subtasks found after wire subtasks — wire must be last")

        # Check for circular dependencies between groups
        if self._has_group_cycle(groups):
            issues.append("Circular dependency detected between groups")

        return groups, issues

    def _has_group_cycle(self, groups):
        graph = {g["id"]: g.get("depends_on", []) for g in groups}
        visited = set()
        rec_stack = set()

        def dfs(gid):
            visited.add(gid)
            rec_stack.add(gid)
            for dep in graph.get(gid, []):
                if dep not in visited:
                    if dfs(dep):
                        return True
                elif dep in rec_stack:
                    return True
            rec_stack.discard(gid)
            return False

        for g in groups:
            if g["id"] not in visited:
                if dfs(g["id"]):
                    return True
        return False

    # --- Flatten for execution ---

    def flatten(self, task_tree):
        """Flatten tree into ordered list of subtasks for the Executor.
        
        Ordering: ALL build tasks first (topo-sorted by group), then ALL wire tasks 
        (topo-sorted by group). This ensures wire tasks see the actual code from all 
        build tasks, not just their own group.
        """
        groups = json.loads(task_tree) if isinstance(task_tree, str) else task_tree
        sorted_groups = self._topo_sort(groups)

        build_tasks = []
        wire_tasks = []

        for group in sorted_groups:
            for subtask in group.get("subtasks", []):
                task_entry = {
                    "id": subtask["id"],
                    "title": subtask["title"],
                    "description": subtask["description"],
                    "model": subtask["model"],
                    "phase": subtask.get("phase", "build"),
                    "file": group["file"],
                    "group_id": group["id"],
                    "group_title": group["title"]
                }

                if subtask.get("phase") == "wire":
                    wire_tasks.append(task_entry)
                else:
                    build_tasks.append(task_entry)

        return build_tasks + wire_tasks

    def _topo_sort(self, groups):
        group_map = {g["id"]: g for g in groups}
        visited = set()
        result = []

        def visit(gid):
            if gid in visited:
                return
            visited.add(gid)
            group = group_map.get(gid)
            if group:
                for dep in group.get("depends_on", []):
                    visit(dep)
                result.append(group)

        for g in groups:
            visit(g["id"])

        return result