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

    def __init__(self, name, system_prompt, llm, tool_executor=None, event_bus=None):
        super().__init__(name, system_prompt, llm, event_bus=event_bus)
        self.tool_executor = tool_executor

    def plan_task(self, task, workspace_context_bus, retry_feedback=None, file_context=None, sibling_context=None):
        self._emit_turn_start(f"Planning task {task.get('id', '')}")
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

        # For .svelte/.vue files, force create_file with complete rewrite
        target_file = task.get("file", "")
        svelte_block = ""
        if target_file and any(target_file.endswith(ext) for ext in (".svelte", ".vue")):
            svelte_block = (
                "\nCRITICAL — SVELTE/VUE FILE:\n"
                "This file uses <script> blocks. Do NOT use multiple smart_edit calls.\n"
                "Instead, use a SINGLE create_file with the COMPLETE file content (script + markup + style).\n"
                "Copy the existing content from the TARGET file above, apply your changes, and write the full file.\n"
                "This overwrites the file — include ALL existing code plus your additions.\n"
            )

        sibling_block = ""
        if sibling_context:
            sibling_block = f"\n{sibling_context}"

        feedback_block = ""
        if retry_feedback:
            feedback_block = f"\nPREVIOUS ATTEMPT FAILED:\n{retry_feedback}\nFix this issue."

        base_prompt = f"""
            {file_block}
            {svelte_block}
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
                        self._emit_turn_end(output_summary=f"Plan: {len(parsed['actions'])} actions")
                        return parsed

                missing = [k for k in ("actions", "reasoning", "summary") if k not in parsed]
                print(f"  [RETRY {attempt+1}] Missing fields: {missing}. Got keys: {list(parsed.keys())}")

            except json.JSONDecodeError as e:
                print(f"  [RETRY {attempt+1}] Invalid JSON: {e}")

        self._emit_turn_end(output_summary="Plan generation failed after 3 attempts")
        return {"reasoning": "Failed to generate valid plan", "actions": [], "summary": "Plan generation failed after 3 attempts"}

    def code_task(self, plan, workspace_path):
        self._emit_turn_start(f"Executing {len(plan.get('actions', []))} actions")
        if "actions" not in plan or not isinstance(plan["actions"], list):
            print(f"  [ERROR] Invalid plan: missing 'actions'. Got keys: {list(plan.keys())}")
            self._emit_turn_end(output_summary="Invalid plan format")
            return {"summary": plan.get("summary", "Invalid plan"), "results": [], "success": False}

        # Safety net: reject multiple smart_edits to .svelte/.vue files
        svelte_edits = {}
        for action in plan["actions"]:
            p = action.get("path", "")
            if action.get("tool") == "smart_edit" and any(p.endswith(ext) for ext in (".svelte", ".vue")):
                svelte_edits[p] = svelte_edits.get(p, 0) + 1
        
        for p, count in svelte_edits.items():
            if count >= 3:
                print(f"  [REJECTED] {count} smart_edits to {p} — use create_file for .svelte/.vue")
                self._emit_turn_end(output_summary=f"Rejected: {count} smart_edits to {p}")
                return {
                    "summary": plan.get("summary", ""),
                    "results": [{"tool": "smart_edit", "result": {
                        "success": False,
                        "error": f"Multiple smart_edits ({count}) to {p} rejected. Use a single create_file with the complete file content instead."
                    }}],
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

            # Validate path for tools that need it
            if tool in ("create_file", "read_context", "smart_edit"):
                if not path:
                    results.append({"tool": tool, "result": {"success": False, "error": "Missing path"}})
                    print(f"  [SKIPPED] {tool}: missing path")
                    continue

            # Dispatch — use ToolExecutor if available, otherwise direct calls
            if self.tool_executor:
                if tool == "create_file":
                    result = self.tool_executor.execute(tool, agent_name=self.name,
                        path=path, content=action.get("content", ""), workspace_path=workspace_path)
                elif tool == "read_context":
                    result = self.tool_executor.execute(tool, agent_name=self.name,
                        path=path, workspace_path=workspace_path)
                elif tool == "smart_edit":
                    edit_action = action.get("action", "add_function")
                    if edit_action == "create":
                        result = self.tool_executor.execute("create_file", agent_name=self.name,
                            path=path, content=action.get("content", ""), workspace_path=workspace_path)
                    else:
                        result = self.tool_executor.execute(tool, agent_name=self.name,
                            path=path, action=edit_action, content=action.get("content", ""),
                            target=action.get("target"), class_name=action.get("class_name"),
                            workspace_path=workspace_path)
                else:
                    result = {"success": False, "error": f"Unknown tool: {tool}. Available: create_file, read_context, smart_edit"}
            else:
                # Direct calls (backward compatible)
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
        success = len(write_results) > 0 and all(r["result"]["success"] for r in write_results)
        self._emit_turn_end(output_summary=f"success={success}, writes={len(write_results)}")
        return {
            "summary": plan.get("summary", ""),
            "results": results,
            "success": success
        }