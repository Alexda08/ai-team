import os, re, json
from agents.base_agent import BaseAgent
from common.tools import read_file, list_files
from common.utils import Utils

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_PATH = os.path.join(BASE_DIR, "workspace")

FINAL_VALIDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["PASSED", "FAILED"]},
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "method": {"type": "string"},
                    "issue": {"type": "string"},
                    "action": {"type": "string", "enum": ["replace_function", "add_function", "add_import", "create"]},
                    "description": {"type": "string"}
                },
                "required": ["file", "issue", "action", "description"]
            }
        }
    },
    "required": ["status", "fixes"]
}

class ValidatorAgent(BaseAgent):

    def __init__(self, name, system_prompt, llm):
        super().__init__(name, system_prompt, llm)

    def validate_task(self, task, coder_result):
        written_files = self._get_written_files(coder_result)
        if not written_files:
            return {"status": "FAILED", "issues": "No files were written by the CODER.", "files_checked": []}

        file_contents = {}
        for file_path in written_files:
            result = read_file(file_path, WORKSPACE_PATH)
            if result["success"]:
                file_contents[file_path] = result["content"]
            else:
                file_contents[file_path] = f"[COULD NOT READ: {result['error']}]"

        prompt = self._build_task_validation_prompt(task, file_contents)
        response = self.llm.generate(system=self.system_prompt, messages=[{"role": "user", "content": prompt}])
        return self._parse_response(response, written_files)

    def validate_project(self, tasks, completed_tasks):
        all_files = list_files(".", WORKSPACE_PATH)
        if not all_files["success"]:
            return {"status": "FAILED", "fixes": []}

        file_contents = {}
        for file_path in all_files.get("files", []):
            if file_path.endswith(('.py', '.json', '.yaml', '.yml', '.txt')):
                result = read_file(file_path, WORKSPACE_PATH)
                if result["success"]:
                    file_contents[file_path] = result["content"]

        task_summary = "\n".join(
            f"- Task {t['id']}: {t['title']} — {t['description']}"
            for t in tasks if t["id"] in completed_tasks
        )

        prompt = f"""
            FINAL PROJECT VALIDATION

            Tasks completed:
            {task_summary}

            Workspace files:
            {chr(10).join(f'=== {p} ==={chr(10)}{c}{chr(10)}' for p, c in file_contents.items())}

            Check:
            1. Imports resolve correctly between files
            2. Method calls match existing signatures (name, params, return type)
            3. Attribute access matches actual class/dataclass fields
            4. No duplicate or conflicting definitions
            5. Entry point (main) works end-to-end

            If ALL checks pass: status=PASSED, fixes=[]
            If ANY fail: status=FAILED, one fix per issue. Each fix.description must start with "In <file>: MODIFY <method/class>" and describe the exact change needed.
            Only report real bugs, not style issues.
            Return ONLY valid JSON.
        """

        response = self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
            json_schema=FINAL_VALIDATION_SCHEMA
        )

        # Try strict JSON first
        try:
            parsed = json.loads(Utils.clean_json(response))
            if "status" in parsed and "fixes" in parsed:
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: extract from mixed text/JSON
        fixes = self._extract_fixes_from_text(response)
        if fixes:
            return {"status": "FAILED", "fixes": fixes}

        if "PASSED" in response.upper() and "FAILED" not in response.upper():
            return {"status": "PASSED", "fixes": []}

        print(f"  [WARN] Could not parse validation response")
        return {"status": "FAILED", "fixes": []}

    def build_dynamic_feedback(self, task, res_coder, validation_result):
        actions = [r.get("tool") for r in res_coder.get("results", [])]
        status = validation_result.get("status")
        issues = validation_result.get("issues", "")

        if not any(r.get("tool") == "smart_edit" and r.get("result", {}).get("success")
                for r in res_coder.get("results", [])):
            if "read_file" in actions or "file_summary" in actions:
                return (
                    f"You read files but produced no output. "
                    f"Use smart_edit to modify files. Actions available: "
                    f"'create' for new files, 'add_function' to add methods, "
                    f"'replace_function' to modify existing methods, 'add_import' for imports. "
                    f"Task: {task['description']}"
                )
            return (
                f"No output produced. You MUST use smart_edit. "
                f"For new files: action='create'. "
                f"For existing files: action='add_function' or 'replace_function'. "
                f"Task: {task['description']}"
            )

        smart_edit_failed = any(
            r.get("tool") == "smart_edit" and not r.get("result", {}).get("success")
            for r in res_coder.get("results", [])
        )
        if smart_edit_failed:
            failed_errors = [r["result"].get("error", "") for r in res_coder.get("results", [])
                            if r.get("tool") == "smart_edit" and not r.get("result", {}).get("success")]
            error_text = "; ".join(failed_errors)
            if "not found" in error_text.lower():
                return (
                    f"smart_edit failed: {error_text}. "
                    f"Use file_summary first to check exact function/class names. "
                    f"If the function does not exist, use action='add_function'. "
                    f"Task: {task['description']}"
                )
            return (
                f"smart_edit failed: {error_text}. "
                f"Try a different action: create, add_function, replace_function, add_import, append. "
                f"Task: {task['description']}"
            )

        if status == "FAILED" and issues:
            prompt = f"""The CODER attempted this task but was rejected.
                TASK: {task['title']}
                DESCRIPTION: {task['description']}
                ISSUES: {issues}
                TOOLS USED: {actions}

                Suggest what smart_edit action to use and what to fix. Be specific.
            """

            response = self.llm.generate(system=self.system_prompt, messages=[{"role": "user", "content": prompt}])
            return response

        return f"Previous attempt failed. Fix: {issues}. Task: {task['description']}"

    # ─── Private helpers ───

    def _extract_fixes_from_text(self, response):
        fixes = []
        # Try JSON array in text
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            try:
                items = json.loads(json_match.group())
                for item in items:
                    if isinstance(item, dict):
                        fixes.append({
                            "file": item.get("file", "unknown"),
                            "issue": item.get("fix", item.get("issue", str(item))),
                            "action": self._infer_action(item.get("fix", item.get("issue", ""))),
                            "description": item.get("description", item.get("fix", str(item)))
                        })
                if fixes:
                    return fixes
            except json.JSONDecodeError:
                pass
        # Numbered list fallback
        for match in re.finditer(r'\d+\.\s*\*?\*?(.+?)\*?\*?.*?:\s*(.+?)(?=\n\d+\.|\Z)', response, re.DOTALL):
            file_match = re.search(r'[`"]?([\w/]+\.py)[`"]?', match.group(0))
            fixes.append({
                "file": file_match.group(1) if file_match else "unknown",
                "issue": match.group(1).strip()[:200],
                "action": "replace_function",
                "description": f"In {file_match.group(1) if file_match else 'unknown'}: MODIFY {match.group(2).strip()[:200]}"
            })
        return fixes

    def _infer_action(self, text):
        t = text.lower()
        if "add import" in t or "missing import" in t:
            return "add_import"
        if "add" in t and ("method" in t or "function" in t):
            return "add_function"
        if "create" in t and "file" in t:
            return "create"
        return "replace_function"

    def _build_task_validation_prompt(self, task, file_contents):
        files_block = "\n".join(f"=== {path} ===\n{content}\n=== END ===" for path, content in file_contents.items())
        return f"""TASK TO VALIDATE:
            Title: {task["title"]}
            Description: {task["description"]}
            Type: {task.get("type", "backend")}

            CODE PRODUCED:
            {files_block}

            Does the code fully implement what the task description requires?
        """

    def _get_written_files(self, coder_result):
        written = []
        for r in coder_result.get("results", []):
            if r.get("tool") == "smart_edit" and r.get("result", {}).get("success"):
                path = r["result"].get("path")
                if path:
                    written.append(path)
        return written

    def _parse_response(self, response, files_checked):
        status_match = re.search(r"STATUS\s*:\s*(PASSED|FAILED)", response, re.IGNORECASE)
        if not status_match:
            return {"status": "FAILED", "issues": f"Unparseable: {response[:200]}", "files_checked": files_checked}

        status = status_match.group(1).upper()
        if status == "PASSED":
            reason_match = re.search(r"REASON\s*:\s*(.+)", response, re.IGNORECASE)
            return {"status": "PASSED", "reason": reason_match.group(1).strip() if reason_match else "Passed", "files_checked": files_checked}
        else:
            issues_match = re.search(r"ISSUES\s*:\s*(.+)", response, re.IGNORECASE | re.DOTALL)
            return {"status": "FAILED", "issues": issues_match.group(1).strip() if issues_match else "Unknown issues", "files_checked": files_checked}