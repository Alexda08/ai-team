import json, platform, os
from agents.base_agent import BaseAgent
from common.tools import read_file, read_file_lines, file_summary, list_files, smart_edit, delete_file, run_command
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
                        "enum": ["read_file", "read_file_lines", "file_summary", "list_files", "smart_edit", "delete_file", "run_command"]
                    },
                    "action": {"type": "string"},
                    "target": {"type": "string"},
                    "class_name": {"type": "string"},
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "command": {"type": "string"},
                    "start": {"type": "integer"},
                    "end": {"type": "integer"}
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

    def plan_task(self, task, workspace_context_bus, retry_feedback=None, file_context=None, sibling_context=None):
        context_block = "WORKSPACE: Empty — no files exist yet."

        if workspace_context_bus and workspace_context_bus.history():
            recent = workspace_context_bus.history()[-5:]
            summaries = "\n".join(f"- {s}" for s in recent)
            context_block = f"WORKSPACE (recent implementations — do NOT duplicate):\n{summaries}"

        file_block = ""
        if file_context:
            file_block = f"\n{file_context}\nUse EXACT method names and signatures shown above. Do NOT invent methods that don't exist. Do NOT call file_summary — you already have all file states."

        sibling_block = ""
        if sibling_context:
            sibling_block = f"\n{sibling_context}"

        feedback_block = ""
        if retry_feedback:
            feedback_block = f"\nPREVIOUS ATTEMPT FAILED:\n{retry_feedback}\nFix this issue."

        base_prompt = f"""
            {context_block}
            {file_block}
            {sibling_block}

            TASK:
            Title: {task["title"]}
            Description: {task["description"]}
            Type: {task.get("type", "backend")}

            OS: {OS_INFO}
            {feedback_block}
            Implement this task. Return ONLY valid JSON.
        """

        for attempt in range(3):
            raw = self.llm.generate(
                system=self.system_prompt,
                messages=[{"role": "user", "content": base_prompt}],
                json_schema=CODE_TASK_SCHEMA
            )

            try:
                parsed = json.loads(Utils.clean_json(raw))

                if not isinstance(parsed, dict):
                    print(f"  [RETRY {attempt+1}] Expected object, got {type(parsed).__name__}")
                    continue

                if "actions" in parsed and "reasoning" in parsed and "summary" in parsed:
                    if isinstance(parsed["actions"], list) and len(parsed["actions"]) > 0:
                        return parsed

                missing = [k for k in ("actions", "reasoning", "summary") if k not in parsed]
                print(f"  [RETRY {attempt+1}] Missing fields: {missing}. Got keys: {list(parsed.keys())}")

            except json.JSONDecodeError as e:
                print(f"  [RETRY {attempt+1}] Invalid JSON: {e}")

        return {"reasoning": "Failed to generate valid plan", "actions": [], "summary": "Plan generation failed after 3 attempts"}

    def code_task(self, plan, workspace_path):
        if "actions" not in plan or not isinstance(plan["actions"], list):
            print(f"  [ERROR] Invalid plan: missing 'actions'. Got keys: {list(plan.keys())}")
            return {"summary": plan.get("summary", "Invalid plan"), "results": [], "success": False}

        results = []

        for action in plan["actions"]:
            tool = action.get("tool")
            path = action.get("path")

            # Block absolute paths
            if path and os.path.isabs(path):
                results.append({"tool": tool, "result": {"success": False, "error": f"Absolute path blocked: {path}"}})
                print(f"  [BLOCKED] {tool}: {path}")
                continue

            # Validate path for tools that need it
            if tool in ("read_file", "read_file_lines", "file_summary", "list_files", "smart_edit", "delete_file"):
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
            if tool == "read_file":
                result = read_file(path, workspace_path)
            elif tool == "read_file_lines":
                result = read_file_lines(path, action.get("start", 1), action.get("end", 50), workspace_path)
            elif tool == "file_summary":
                result = file_summary(path, workspace_path)
            elif tool == "list_files":
                result = list_files(path, workspace_path)
            elif tool == "smart_edit":
                result = smart_edit(
                    path,
                    action.get("action", "create"),
                    action.get("content", ""),
                    target=action.get("target"),
                    class_name=action.get("class_name"),
                    workspace_path=workspace_path
                )
            elif tool == "delete_file":
                result = delete_file(path, workspace_path)
            elif tool == "run_command":
                result = run_command(action["command"], workspace_path)
            else:
                result = {"success": False, "error": f"Unknown tool: {tool}"}

            results.append({"tool": tool, "result": result})

            if not result["success"]:
                if tool in ("read_file", "read_file_lines", "file_summary", "list_files"):
                    print(f"  [WARN] {tool}: {result['error']} (non-fatal)")
                else:
                    print(f"  [FAILED] {tool}: {result['error']}")
            else:
                print(f"  [OK] {tool}: {action.get('path') or action.get('command')}")

        critical_results = [r for r in results if r["tool"] not in ("read_file", "read_file_lines", "file_summary", "list_files")]
        return {
            "summary": plan.get("summary", ""),
            "results": results,
            "success": len(critical_results) > 0 and all(r["result"]["success"] for r in critical_results)
        }