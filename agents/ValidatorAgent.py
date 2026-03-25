import os, re
from agents.base_agent import BaseAgent
from common.tools import read_file, list_files

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_PATH = os.path.join(BASE_DIR, "workspace")


class ValidatorAgent(BaseAgent):

    def __init__(self, name, system_prompt, llm):
        super().__init__(name, system_prompt, llm)

    def validate_task(self, task, coder_result):
        """Validate a single task after CODER execution."""
        # Gather files that were written
        written_files = self._get_written_files(coder_result)

        if not written_files:
            return {
                "status": "FAILED",
                "issues": "No files were written by the CODER. Task produced no output.",
                "files_checked": []
            }

        # Read the actual file contents
        file_contents = {}
        for file_path in written_files:
            result = read_file(file_path, WORKSPACE_PATH)
            if result["success"]:
                file_contents[file_path] = result["content"]
            else:
                file_contents[file_path] = f"[COULD NOT READ: {result['error']}]"

        # Build validation prompt
        prompt = self._build_task_validation_prompt(task, file_contents)

        response = self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}]
        )

        return self._parse_response(response, written_files)

    def validate_project(self, tasks, completed_tasks):
        """Final validation: check overall project coherence."""
        # List all files in workspace
        all_files = list_files(".", WORKSPACE_PATH)
        if not all_files["success"]:
            return {"status": "FAILED", "issues": "Could not read workspace"}

        # Read all code files
        file_contents = {}
        for file_path in all_files.get("files", []):
            if file_path.endswith(('.py', '.json', '.yaml', '.yml', '.txt')):
                result = read_file(file_path, WORKSPACE_PATH)
                if result["success"]:
                    file_contents[file_path] = result["content"]

        # Build summary of what was supposed to be built
        task_summary = "\n".join(
            f"- Task {t['id']}: {t['title']} — {t['description']}"
            for t in tasks if t["id"] in completed_tasks
        )

        prompt = f"""FINAL PROJECT VALIDATION

            These tasks were completed:
            {task_summary}

            These files exist in the workspace:
            {chr(10).join(f'=== {path} ===' + chr(10) + content + chr(10) for path, content in file_contents.items())}

            Verify:
            1. All tasks produced their expected output
            2. Files reference each other correctly (imports work)
            3. No task overwrote another task's work
            4. The project could run without obvious errors

            Return your assessment.
        """

        response = self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}]
        )

        return self._parse_response(response, list(file_contents.keys()))

    # En ValidatorAgent

    def build_dynamic_feedback(self, task, res_coder, validation_result):
        actions = [r.get("tool") for r in res_coder.get("results", [])]
        status = validation_result.get("status")
        issues = validation_result.get("issues", "")

        # No output at all
        if not any(r.get("tool") == "smart_edit" and r.get("result", {}).get("success")
                for r in res_coder.get("results", [])):

            if "read_file" in actions or "file_summary" in actions:
                return (
                    f"You read files but produced no output. "
                    f"Use smart_edit to modify files. Actions available: "
                    f"'create' for new files, 'add_function' to add methods, "
                    f"'replace_function' to modify existing methods, 'add_import' for imports. "
                    f"You only need to provide the NEW code, not the entire file. "
                    f"Task: {task['description']}"
                )
            return (
                f"No output produced. You MUST use smart_edit. "
                f"For new files: action='create'. "
                f"For existing files: action='add_function' or 'replace_function'. "
                f"Task: {task['description']}"
            )

        # smart_edit failed (e.g. replace_function couldn't find target)
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
                    f"If the function does not exist, use action='add_function' instead of 'replace_function'. "
                    f"If the class is not found, check the file path is correct. "
                    f"Task: {task['description']}"
                )
            return (
                f"smart_edit failed: {error_text}. "
                f"Try a different action. Available: create, add_function, replace_function, "
                f"add_import, add_to_class, insert_at, append. "
                f"Task: {task['description']}"
            )

        # Validator rejected the code
        if status == "FAILED" and issues:
            prompt = f"""
                The CODER attempted this task but the implementation was rejected.
                TASK: {task['title']}
                DESCRIPTION: {task['description']}

                ISSUES FOUND: {issues}

                TOOLS USED: {actions}

                The CODER should use smart_edit with these available actions:
                - create: new file
                - add_function: add method (use class_name to target a class)
                - replace_function: replace existing method (use target for function name)
                - add_import: add import statement
                - append: add to end of file

                Suggest what the CODER should do differently. Be specific about which smart_edit action to use and what to fix.
            """

            response = self.llm.generate(
                system=self.system_prompt,
                messages=[{"role": "user", "content": prompt}]
            )
            return response

        return f"Previous attempt failed. Fix these issues: {issues}. Task: {task['description']}"

    def _build_task_validation_prompt(self, task, file_contents):
        """Build the prompt for single-task validation."""
        files_block = "\n".join(
            f"=== {path} ===\n{content}\n=== END ==="
            for path, content in file_contents.items()
        )

        return f"""
            TASK TO VALIDATE:
            Title: {task["title"]}
            Description: {task["description"]}
            Type: {task["type"]}

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
        """Parse VALIDATOR response into structured result."""
        status_match = re.search(r"STATUS\s*:\s*(PASSED|FAILED)", response, re.IGNORECASE)

        if not status_match:
            return {
                "status": "FAILED",
                "issues": f"Validator returned unparseable response: {response[:200]}",
                "files_checked": files_checked
            }

        status = status_match.group(1).upper()

        if status == "PASSED":
            reason_match = re.search(r"REASON\s*:\s*(.+)", response, re.IGNORECASE)
            return {
                "status": "PASSED",
                "reason": reason_match.group(1).strip() if reason_match else "Validation passed",
                "files_checked": files_checked,
            }
        else:
            issues_match = re.search(r"ISSUES\s*:\s*(.+)", response, re.IGNORECASE | re.DOTALL)
            # TODO: build interactive feedback based on issues
            return {
                "status": "FAILED",
                "issues": issues_match.group(1).strip() if issues_match else "Unknown issues",
                "files_checked": files_checked
            }

