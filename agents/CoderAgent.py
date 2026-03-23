import json, platform, os
from agents.base_agent import BaseAgent
from common.tools import read_file, write_file, list_files, delete_file, run_command

OS_INFO = platform.system()
CODE_TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {
                        "type": "string",
                        "enum": ["write_file", "read_file", "list_files", "delete_file", "run_command"]
                    },
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "command": {"type": "string"}
                },
                "required": ["tool"]
            }
        },
        "summary": {"type": "string"}
    },
    "required": ["reasoning", "actions", "summary"]
}

class CoderAgent(BaseAgent):

    def __init__(self, name, system_prompt, llm):
        super().__init__(name, system_prompt, llm)

    def plan_task(self, task, workspace_context_bus):
        context_block = ""
        
        if workspace_context_bus:
            summaries = "\n".join(f"- {s}" for s in workspace_context_bus.history())
            context_block = f"""
                WORKSPACE CONTEXT (already implemented — do NOT duplicate):
                {summaries}
                ---
            """

        prompt = f"""
            You are a coding agent responsible for implementing exactly ONE task.

            DO NOT implement anything outside the scope of this task.
            DO NOT duplicate code that has already been implemented (see workspace context below).
            Write production-quality code — no placeholders, no pseudocode, no TODO comments.

            {context_block}
            TASK TO IMPLEMENT:

            Title: {task["title"]}
            Description: {task["description"]}
            Type: {task["type"]}

            ---

            TOOLS AVAILABLE:

            Use these tools to interact with the filesystem and execute commands:

            - write_file: Write content to a file at the given path. Requires "path" and "content".
            - read_file: Read the content of a file at the given path. Requires "path".
            - list_files: List files in a directory. Requires "path".
            - delete_file: Delete a file at the given path. Requires "path".
            - run_command: Execute a shell command. Requires "command".

            ---

            ENVIRONMENT:

            - Operating System: {OS_INFO}
            - Use OS-appropriate commands if run_command is strictly necessary.
            - On Windows: use 'mkdir', 'copy', 'del' — NOT 'mkdir -p', 'cp', 'rm'
            - On Linux/Mac: use standard Unix commands
            - ALWAYS prefer write_file over run_command for file/directory operations.
            ---

            OUTPUT RULES:

            - "reasoning": Explain your plan before acting. What files will you create or modify, and why.
            - "actions": The ordered list of tool calls needed to fully implement the task.
            - "summary": A concrete description of what the actions accomplish.

            Return ONLY valid JSON. Do NOT wrap in markdown.
        """

        raw = self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
            json_schema=CODE_TASK_SCHEMA
        )

        return json.loads(raw)

    def code_task(self, plan, workspace_path):
        results = []

        for action in plan["actions"]:
            tool = action.get("tool")

            # Enforce workspace path — inject it into all path-based actions
            path = action.get("path")
            if path:
                # Ensure path is always relative to workspace, never absolute
                if os.path.isabs(path):
                    results.append({"tool": tool, "result": {"success": False, "error": f"Absolute paths are not allowed: {path}"}})
                    print(f"  [BLOCKED] {tool}: absolute path rejected: {path}")
                    continue

            # Validate required fields before dispatching
            if tool in ("write_file", "read_file", "list_files", "delete_file"):
                if "path" not in action or not action["path"]:
                    results.append({"tool": tool, "result": {"success": False, "error": f"Missing required field for {tool}"}})
                    print(f"  [SKIPPED] {tool}: missing required field")
                    continue
            elif tool == "run_command":
                if "command" not in action or not action["command"]:
                    results.append({"tool": tool, "result": {"success": False, "error": f"Missing required field for {tool}"}})
                    print(f"  [SKIPPED] {tool}: missing required field")
                    continue

            if tool == "write_file":
                result = write_file(action["path"], action.get("content", ""), workspace_path)
            elif tool == "read_file":
                result = read_file(action["path"], workspace_path)
            elif tool == "list_files":
                result = list_files(action.get("path", "."), workspace_path)
            elif tool == "delete_file":
                result = delete_file(action["path"], workspace_path)
            elif tool == "run_command":
                result = run_command(action["command"], workspace_path)
            else:
                result = {"success": False, "error": f"Unknown tool: {tool}"}

            results.append({"tool": tool, "result": result})

            if not result["success"]:
                print(f"  [FAILED] {tool}: {result['error']}")
            else:
                print(f"  [OK] {tool}: {action.get('path') or action.get('command')}")

        return {
            "summary": plan["summary"],
            "results": results,
            "success": all(r["result"]["success"] for r in results)
        }
