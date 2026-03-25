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
            if r.get("tool") in ("write_file", "append_file", "replace_in_file") and r.get("result", {}).get("success"):
                path = r["result"].get("path")
                if path:
                    written.append(path)
        return written

    # def _get_dinamic_feedback(self, issues):
    #     prompt = f"""
    #         Given the following validation issues:
    #         {issues}

    #         Return your assessment.
    #     """

    #     response = self.llm.generate(
    #         system=self.system_prompt,
    #         messages=[{"role": "user", "content": prompt}]
    #     )

    #     return response

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