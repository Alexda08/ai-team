import os, json, shutil
from agents.base_agent import BaseAgent
from agents.CoderAgent import CoderAgent
from agents.ValidatorAgent import ValidatorAgent
from common.utils import Utils

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_PATH = os.path.join(BASE_DIR, "workspace")

class ExecutorAgent(BaseAgent):
    ESCALATION_CHAIN = {"light": "medium", "medium": "heavy", "heavy": "ultra"}

    def __init__(self, name, system_prompt, llm, model_map=None, coder_prompt=None, validator_prompt=None):
        super().__init__(name, system_prompt, llm)
        self.coder_pool = {}
        self.coder_prompt = coder_prompt
        self.validator = None
        self.max_retries = 2

        if model_map and coder_prompt:
            self._build_coder_pool(model_map)
        
        if validator_prompt:
            self.validator = ValidatorAgent(
                name="Validator",
                system_prompt=validator_prompt,
                llm=llm
            )
            Utils.console_print("Validator ready", "green", bold=True)

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

    def _attempt_task(self, task, coder, workspace_context_bus, retry_feedback=None):
        # Try to execute a task with retries. Returns (success, result, last_feedback).
        res_coder = None

        for attempt in range(self.max_retries + 1):
            action_plan = coder.plan_task(task, workspace_context_bus, retry_feedback)
            print(f"  [PLAN] actions: {[a.get('tool') for a in action_plan.get('actions', [])]}")
            res_coder = coder.code_task(action_plan, WORKSPACE_PATH)

            if not res_coder["success"]:
                actions_used = [a.get("tool") for a in action_plan.get("actions", [])]
                # TODO: improve feedback based on what was missing or incorrect in the action plan
                retry_feedback = (
                    f"FAILED: You only used {actions_used}. "
                    f"You MUST include write_file actions. "
                    f"Task: {task['description']}"
                )
                print(f"  [FAILED] Coder produced no output on attempt {attempt + 1}")
                
                if attempt < self.max_retries:
                    print(f"  [RETRY] Attempt {attempt + 2}/{self.max_retries + 1}")
                continue

            if self.validator:
                validation = self.validator.validate_task(task, res_coder)
                print(f"  [VALIDATE] {validation['status']}")

                if validation["status"] == "PASSED":
                    print(f"  [PASSED] {validation.get('reason', '')}")
                    return True, {"task_id": task["id"], "summary": res_coder["summary"], "success": True}, None

                retry_feedback = validation.get("issues", "Validation failed")
                print(f"  [ISSUES] {retry_feedback}")
                if attempt < self.max_retries:
                    print(f"  [RETRY] Attempt {attempt + 2}/{self.max_retries + 1}")
                continue

            return True, {"task_id": task["id"], "summary": res_coder["summary"], "success": res_coder["success"]}, None

        return False, res_coder, retry_feedback

    def clean_workspace(self):
        if os.path.exists(WORKSPACE_PATH):
            shutil.rmtree(WORKSPACE_PATH)
        os.makedirs(WORKSPACE_PATH)

    def run_task(self, task, workspace_context_bus, completed_tasks):
        print(f"\nExecuting task {task['id']}: {task['title']} [{task.get('model', 'medium')}]")

        current_model = task.get("model", "medium")
        coder = self._get_coder(task)
        print(f"  Using: {coder.name} ({coder.llm.provider}:{coder.llm.model})")

        # First attempt with assigned model
        success, result, last_feedback = self._attempt_task(task, coder, workspace_context_bus)
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

            escalation_feedback = (
                f"A weaker model failed this task. "
                f"Implement it completely. "
                f"Previous failure: {last_feedback}"
            )

            success, result, last_feedback = self._attempt_task(task, escalated_coder, workspace_context_bus, escalation_feedback)
            if success:
                return result

            tier = next_tier

        print(f"  [EXHAUSTED] Task {task['id']} failed after all tiers")
        return {"task_id": task["id"], "summary": "Failed after all escalation tiers", "success": False}

    def run_final_validation(self, tasks, completed_tasks):
        """Run final project-wide validation."""
        if not self.validator:
            print("  [SKIP] No validator configured")
            return {"status": "SKIPPED"}

        print("\n  Running final project validation...")
        result = self.validator.validate_project(tasks, completed_tasks)
        print(f"  [FINAL] {result['status']}")

        if result["status"] == "FAILED":
            print(f"  [ISSUES] {result.get('issues', '')}")

        return result
