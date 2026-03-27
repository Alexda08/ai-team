import os, json, shutil
from agents.base_agent import BaseAgent
from agents.CoderAgent import CoderAgent
from agents.ValidatorAgent import ValidatorAgent
from common.utils import Utils
from runtime.message_bus import MessageBus

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_PATH = os.path.join(BASE_DIR, "workspace")

class ExecutorAgent(BaseAgent):
    ESCALATION_CHAIN = {"light": "medium", "medium": "heavy", "heavy": "ultra"}

    def __init__(self, name, system_prompt, llm, model_map=None, coder_prompt=None):
        super().__init__(name, system_prompt, llm)
        self.coder_pool = {}
        self.coder_prompt = coder_prompt
        self.max_retries = 2
        self.all_tasks = []

        if model_map and coder_prompt:
            self._build_coder_pool(model_map)

    def _build_coder_pool(self, model_map):
        seen_llms = {}

        for size, llm in model_map.items():
            llm_key = f"{llm.provider}:{llm.model}"

            if llm_key not in seen_llms:
                coder = CoderAgent(
                    name=f"Coder-{size}",
                    system_prompt=self.coder_prompt,
                    llm=llm
                )
                seen_llms[llm_key] = coder
                Utils.console_print(f"  Created Coder-{size} ({llm_key})", "yellow")

            self.coder_pool[size] = seen_llms[llm_key]

        Utils.console_print(f"  Coder pool ready: {list(self.coder_pool.keys())} ({len(seen_llms)} unique LLMs)", "yellow", bold=True)

    def _get_coder(self, task):
        # Get the appropriate Coder for a task's model size.
        model_size = task.get("model", "medium")

        if model_size in self.coder_pool:
            return self.coder_pool[model_size]

        # Fallback: try medium, then any available
        if "medium" in self.coder_pool:
            print(f"  [WARN] No coder for '{model_size}', falling back to medium")
            return self.coder_pool["medium"]

        if self.coder_pool:
            fallback = next(iter(self.coder_pool.values()))
            print(f"  [WARN] No coder for '{model_size}', falling back to {fallback.name}")
            return fallback

        raise RuntimeError("No coders available in pool")

    def _get_escalated_coder(self, current_model):
        next_tier = self.ESCALATION_CHAIN.get(current_model)
        
        if next_tier and next_tier in self.coder_pool:
            return self.coder_pool[next_tier], next_tier
        return None, None

    def _attempt_task(self, task, coder, validator, workspace_context_bus, retry_feedback=None, sibling_context=None):
        res_coder = None
        feedback_history = []
        
        if retry_feedback:
            feedback_history.append(retry_feedback)

        for attempt in range(self.max_retries + 1):
            current_feedback = "\n".join(feedback_history) if feedback_history else None
            file_context = self._get_file_context(task)

            action_plan = coder.plan_task(task, workspace_context_bus, current_feedback, file_context, sibling_context=sibling_context)
            print(f"  [PLAN] actions: {[a.get('tool') for a in action_plan.get('actions', [])]}")
            res_coder = coder.code_task(action_plan, WORKSPACE_PATH)

            if not res_coder["success"]:
                actions_used = [a.get("tool") for a in action_plan.get("actions", [])]
                smart_edit_used = "smart_edit" in actions_used
                read_used = any(a in actions_used for a in ("read_file", "file_summary", "read_file_lines"))

                # smart_edit was used but failed
                if smart_edit_used:
                    failed_errors = [
                        r["result"].get("error", "")
                        for r in res_coder.get("results", [])
                        if r.get("tool") == "smart_edit"
                        and not r.get("result", {}).get("success")
                    ]
                    error_text = "; ".join(failed_errors)

                    if "not found" in error_text.lower():
                        new_feedback = (
                            f"smart_edit failed: {error_text}. "
                            f"The function/class does not exist yet. "
                            f"Use action='add_function' instead of 'replace_function'. "
                            f"If adding to a class, include 'class_name'. "
                            f"Example: {{\"tool\": \"smart_edit\", \"path\": \"<file>\", \"action\": \"add_function\", \"class_name\": \"<ClassName>\", \"content\": \"<your code>\"}} "
                            f"Task: {task['description']}"
                        )
                    else:
                        new_feedback = (
                            f"smart_edit failed: {error_text}. "
                            f"Try a different action: create, add_function, replace_function, add_import, append. "
                            f"Task: {task['description']}"
                        )

                # Only reads, no smart_edit
                elif read_used and not smart_edit_used:
                    new_feedback = (
                        f"You used {actions_used} but never called smart_edit. "
                        f"After reading the file, you MUST call smart_edit to make changes. "
                        f"Example: {{\"tool\": \"smart_edit\", \"path\": \"<file>\", \"action\": \"add_function\", \"class_name\": \"<ClassName>\", \"content\": \"<your code>\"}} "
                        f"Task: {task['description']}"
                    )

                # No actions at all
                elif not actions_used:
                    new_feedback = (
                        f"No actions produced. Use smart_edit: "
                        f"action='create' for new files, action='add_function' for existing files. "
                        f"Task: {task['description']}"
                    )

                # Fallback
                else:
                    new_feedback = validator.build_dynamic_feedback(task, res_coder, {"status": "FAILED", "issues": "No output produced"})

                feedback_history.append(f"Attempt {attempt + 1}: {new_feedback}")
                print(f"  [FAILED] Coder produced no output on attempt {attempt + 1}")
                print(f"  [FEEDBACK] {new_feedback[:150]}")
                if attempt < self.max_retries:
                    print(f"  [RETRY] Attempt {attempt + 2}/{self.max_retries + 1}")
                continue

            if validator:
                validation = validator.validate_task(task, res_coder)
                print(f"  [VALIDATE] {validation['status']}")

                if validation["status"] == "PASSED":
                    print(f"  [PASSED] {validation.get('reason', '')}")
                    return True, {"task_id": task["id"], "summary": res_coder["summary"], "success": True}, None

                new_feedback = validator.build_dynamic_feedback(task, res_coder, validation)
                feedback_history.append(f"Attempt {attempt + 1}: {new_feedback}")
                print(f"  [FEEDBACK] {new_feedback[:200]}")
                if attempt < self.max_retries:
                    print(f"  [RETRY] Attempt {attempt + 2}/{self.max_retries + 1}")
                continue

            return True, {"task_id": task["id"], "summary": res_coder["summary"], "success": res_coder["success"]}, None

        # Return last feedback for escalation
        last_feedback = feedback_history[-1] if feedback_history else "All attempts failed"
        return False, res_coder, last_feedback

    def _get_sibling_context(self, task, completed_ids):
        gid = task.get("group_id")
        if not gid:
            return None
        siblings = [
            f"- [{t['id']}] {t['title']}: {t['description'][:150]}"
            for t in self.all_tasks
            if t.get("group_id") == gid and t["id"] != task["id"] and t["id"] in completed_ids
        ]
        if not siblings:
            return None
        return "COMPLETED TASKS IN THIS GROUP (use their signatures/names):\n" + "\n".join(siblings)
    
    def _get_file_context(self, task):
        from common.tools import file_summary, list_files
        target = task.get("file")
        files = list_files(".", WORKSPACE_PATH)
        if not files["success"]:
            return None
        summaries = []
        for f in files.get("files", []):
            if not f.endswith(('.py', '.js', '.ts', '.json')):
                continue
            result = file_summary(f, WORKSPACE_PATH)
            if result["success"]:
                prefix = ">>> TARGET" if target and f.endswith(target) else "    "
                summaries.append(f"{prefix} {f}:\n{result['content']}")
        if not summaries:
            return None
        return "WORKSPACE FILES (use these exact signatures):\n" + "\n".join(summaries)

    def _get_workspace_summary(self):
        """List files in workspace with their function/class signatures."""
        from common.tools import file_summary, list_files
        files = list_files(".", WORKSPACE_PATH)
        if not files["success"]:
            return "Could not read workspace"
        
        summaries = []
        for f in files.get("files", []):
            if f.endswith(('.py', '.js', '.ts')):
                result = file_summary(f, WORKSPACE_PATH)
                if result["success"]:
                    summaries.append(f"--- {f} ---\n{result['content']}")
        return "\n".join(summaries)

    def clean_workspace(self):
        if os.path.exists(WORKSPACE_PATH):
            shutil.rmtree(WORKSPACE_PATH)
        os.makedirs(WORKSPACE_PATH)

    def run_task(self, task, workspace_context_bus, completed_tasks, validator):
        print(f"\nExecuting task {task['id']}: {task['title']} [{task.get('model', 'medium')}]")

        current_model = task.get("model", "medium")
        coder = self._get_coder(task)
        print(f"  Using: {coder.name} ({coder.llm.provider}:{coder.llm.model})")
        
        # Get context for sibling tasks
        sibling_context = self._get_sibling_context(task, completed_tasks)

        # First attempt with assigned model
        success, result, last_feedback = self._attempt_task(task, coder, validator, workspace_context_bus, sibling_context=sibling_context)
        if success:
            return result

        # Escalate through higher tiers
        tier = current_model

        while tier in self.ESCALATION_CHAIN:
            next_tier = self.ESCALATION_CHAIN[tier]
            if next_tier not in self.coder_pool:
                break

            escalated_coder = self.coder_pool[next_tier]
            print(f"\n  [ESCALATE] {tier} -> {next_tier} ({escalated_coder.llm.provider}:{escalated_coder.llm.model})")

            file_context = self._get_file_context(task) or "File does not exist yet."
            escalation_feedback = (
                f"A weaker model failed this task. "
                f"Implement it completely. "
                f"Previous failure: {last_feedback}\n\n"
                f"{file_context}"
            )

            success, result, last_feedback = self._attempt_task(task, escalated_coder, validator, workspace_context_bus, escalation_feedback, sibling_context=sibling_context)
            if success:
                return result

            tier = next_tier

        print(f"  [EXHAUSTED] Task {task['id']} failed after all tiers")
        return {"task_id": task["id"], "summary": "Failed after all escalation tiers", "success": False}

    def run_final_validation(self, validator, tasks, completed_tasks, max_rounds=3):
        if not validator:
            return {"status": "SKIPPED"}

        result = validator.validate_project(tasks, completed_tasks)
        print(f"  [FINAL] {result['status']}")
        prev_fix_count = None

        for round_num in range(1, max_rounds + 1):
            if result["status"] != "FAILED" or not result.get("fixes"):
                break

            current_fix_count = len(result["fixes"])
            if prev_fix_count is not None and current_fix_count >= prev_fix_count:
                print(f"  [ABORT] Fixes not decreasing ({prev_fix_count} -> {current_fix_count})")
                break

            prev_fix_count = current_fix_count
            print(f"\n  [DEBUG ROUND {round_num}/{max_rounds}] ({current_fix_count} fixes)")

            debug_context_bus = MessageBus()
            for i, fix in enumerate(result["fixes"]):
                fix_task = {
                    "id": f"fix.{round_num}.{i+1}",
                    "title": f"Fix: {fix.get('method', fix['file'])}",
                    "description": fix["description"],
                    "file": fix["file"],
                    "model": "medium",
                    "group_id": f"fix.{round_num}"
                }
                print(f"  [FIX {i+1}/{current_fix_count}] {fix['issue']}")
                task_result = self.run_task(fix_task, debug_context_bus, [], validator)
                if not task_result.get("success"):
                    print(f"  [ERROR] Fix {fix_task['id']} failed, continuing")

            result = validator.validate_project(tasks, completed_tasks)
            print(f"  [REVALIDATE] {result['status']}")

        return result
