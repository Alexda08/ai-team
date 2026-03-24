import json, platform, os
from agents.base_agent import BaseAgent
from common.tools import read_file, write_file, list_files, delete_file, run_command, replace_in_file, append_file
from common.utils import Utils

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
                        "enum": ["write_file", "read_file", "list_files", "delete_file", "run_command", "replace_in_file", "append_file"]
                    },
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
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

    def plan_task(self, task, workspace_context_bus, retry_feedback=None):
        context_block = "WORKSPACE: Empty — no files exist yet."

        if workspace_context_bus and workspace_context_bus.history():
            summaries = "\n".join(f"- {s}" for s in workspace_context_bus.history())
            context_block = f"WORKSPACE (already implemented — do NOT duplicate):\n{summaries}"

        feedback_block = ""
        if retry_feedback:
            feedback_block = f"\n\nPREVIOUS ATTEMPT FAILED:\n{retry_feedback}\nYou MUST fix this issue in your next attempt."

        prompt = f"""
            {context_block}
            TASK:
            Title: {task["title"]}
            Description: {task["description"]}
            Type: {task["type"]}

            OS: {OS_INFO}

            {feedback_block}

            Implement this task. Return ONLY valid JSON.
        """

        for attempt in range(3):
            raw = self.llm.generate(
                system=self.system_prompt,
                messages=[{"role": "user", "content": prompt}],
                json_schema=CODE_TASK_SCHEMA
            )

            try:
                parsed = json.loads(Utils.clean_json(raw))

                if "actions" in parsed and "reasoning" in parsed and "summary" in parsed:
                    if isinstance(parsed["actions"], list) and len(parsed["actions"]) > 0:
                        return parsed

                missing = [k for k in ("actions", "reasoning", "summary") if k not in parsed]
                print(f"  [RETRY {attempt+1}] Missing fields: {missing}. Got keys: {list(parsed.keys())}")
                prompt += f"\n\nPREVIOUS ATTEMPT FAILED: missing fields {missing}. Return JSON with exactly: reasoning, actions, summary."

            except json.JSONDecodeError as e:
                print(f"  [RETRY {attempt+1}] Invalid JSON: {e}")
                prompt += "\n\nPREVIOUS ATTEMPT FAILED: invalid JSON. Return ONLY valid JSON, no markdown."

        return {"reasoning": "Failed to generate valid plan", "actions": [], "summary": "Plan generation failed after 3 attempts"}

    def code_task(self, plan, workspace_path):
        if "actions" not in plan or not isinstance(plan["actions"], list):
            print(f"  [ERROR] Invalid plan: missing 'actions'. Got keys: {list(plan.keys())}")
            return {
                "summary": plan.get("summary", "Invalid plan"),
                "results": [],
                "success": False
            }

        results = []

        for action in plan["actions"]:
            tool = action.get("tool")
            path = action.get("path")

            # Block absolute paths
            if path and os.path.isabs(path):
                results.append({"tool": tool, "result": {"success": False, "error": f"Absolute path blocked: {path}"}})
                print(f"  [BLOCKED] {tool}: {path}")
                continue

            # Validate required fields
            if tool in ("write_file", "read_file", "list_files", "delete_file"):
                if not path:
                    results.append({"tool": tool, "result": {"success": False, "error": "Missing path"}})
                    print(f"  [SKIPPED] {tool}: missing path")
                    continue
            elif tool == "run_command":
                if not action.get("command"):
                    results.append({"tool": tool, "result": {"success": False, "error": "Missing command"}})
                    print(f"  [SKIPPED] {tool}: missing command")
                    continue

            # Dispatch
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
            elif tool == "replace_in_file":
                result = replace_in_file(action["path"], action.get("old_str", ""), action.get("new_str", ""), workspace_path)
            elif tool == "append_file":
                result = append_file(action["path"], action.get("content", ""), workspace_path)
            else:
                result = {"success": False, "error": f"Unknown tool: {tool}"}

            results.append({"tool": tool, "result": result})

            if not result["success"]:
                if tool in ("read_file", "list_files"):
                    print(f"  [WARN] {tool}: {result['error']} (non-fatal)")
                else:
                    print(f"  [FAILED] {tool}: {result['error']}")
            else:
                print(f"  [OK] {tool}: {action.get('path') or action.get('command')}")

        critical_results = [r for r in results if r["tool"] not in ("read_file", "list_files")]
        return {
            "summary": plan.get("summary", ""),
            "results": results,
            "success": len(critical_results) > 0 and all(r["result"]["success"] for r in critical_results)
        }