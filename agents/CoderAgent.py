import json, platform, os
from agents.base_agent import BaseAgent
from common.tools import create_file, read_context, smart_edit
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
                        "enum": ["create_file", "read_context", "smart_edit"]
                    },
                    "action": {"type": "string"},
                    "target": {"type": "string"},
                    "class_name": {"type": "string"},
                    "path": {"type": "string"},
                    "content": {"type": "string"}
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
            file_block = (
                f"\n{file_context}\n"
                f"You already have all file contents above. "
                f"Go DIRECTLY to smart_edit or create_file. "
                f"Do NOT use read_context — the context is already provided."
            )

        sibling_block = ""
        if sibling_context:
            sibling_block = f"\n{sibling_context}"

        feedback_block = ""
        if retry_feedback:
            feedback_block = f"\nPREVIOUS ATTEMPT FAILED:\n{retry_feedback}\nFix this issue."

        base_prompt = f"""
            {file_block}
            {sibling_block}
            {context_block}

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
            if tool in ("create_file", "read_context", "smart_edit"):
                if not path:
                    results.append({"tool": tool, "result": {"success": False, "error": "Missing path"}})
                    print(f"  [SKIPPED] {tool}: missing path")
                    continue

            # Dispatch
            if tool == "create_file":
                result = create_file(
                    path,
                    action.get("content", ""),
                    workspace_path=workspace_path
                )
            elif tool == "read_context":
                result = read_context(path, workspace_path=workspace_path)
            elif tool == "smart_edit":
                edit_action = action.get("action", "add_function")
                # Intercept create action — redirect to create_file
                if edit_action == "create":
                    result = create_file(path, action.get("content", ""), workspace_path=workspace_path)
                else:
                    result = smart_edit(
                        path,
                        edit_action,
                        action.get("content", ""),
                        target=action.get("target"),
                        class_name=action.get("class_name"),
                        workspace_path=workspace_path
                    )
            else:
                result = {"success": False, "error": f"Unknown tool: {tool}. Available: create_file, read_context, smart_edit"}

            results.append({"tool": tool, "result": result})

            if not result.get("success"):
                if tool == "read_context":
                    print(f"  [WARN] {tool}: {result.get('error', 'unknown')} (non-fatal)")
                else:
                    print(f"  [FAILED] {tool}: {result.get('error', 'unknown')}")
            else:
                print(f"  [OK] {tool}: {path}")

        # Success = at least one write operation succeeded
        write_results = [r for r in results if r["tool"] in ("create_file", "smart_edit")]
        return {
            "summary": plan.get("summary", ""),
            "results": results,
            "success": len(write_results) > 0 and all(r["result"]["success"] for r in write_results)
        }