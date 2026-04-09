import os, json, shutil, tempfile
from agents.base_agent import BaseAgent
from agents.CoderAgent import CoderAgent
from agents.ValidatorAgent import ValidatorAgent
from common.utils import Utils
from common.tools import file_summary, list_files, read_file
from runtime.message_bus import MessageBus

# Lazy import to avoid circular dependency
_ClaudeCodeAgent = None
def _get_claude_code_agent():
    global _ClaudeCodeAgent
    if _ClaudeCodeAgent is None:
        from agents.ClaudeCodeAgent import ClaudeCodeAgent
        _ClaudeCodeAgent = ClaudeCodeAgent
    return _ClaudeCodeAgent

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_PATH = os.path.join(BASE_DIR, "workspace")

class ExecutorAgent(BaseAgent):
    ESCALATION_CHAIN = {"light": "medium", "medium": "heavy", "heavy": "ultra"}

    def __init__(self, name, system_prompt, llm, model_map=None, coder_prompt=None,
                 tool_executor=None, loop_guard=None, execution_history=None,
                 claude_code_config=None):
        super().__init__(name, system_prompt, llm)
        self.coder_pool = {}
        self.coder_prompt = coder_prompt
        self.max_retries = 2
        self.all_tasks = []
        self._tool_executor = tool_executor
        self.loop_guard = loop_guard
        self.execution_history = execution_history
        self.claude_code_config = claude_code_config
        self.use_claude_code = claude_code_config is not None and claude_code_config.enabled

        if model_map and (coder_prompt or self.use_claude_code):
            self._build_coder_pool(model_map)

    @property
    def tool_executor(self):
        return self._tool_executor

    @tool_executor.setter
    def tool_executor(self, value):
        """When tool_executor is set, propagate to all coders in the pool."""
        self._tool_executor = value
        for coder in self.coder_pool.values():
            coder.tool_executor = value

    def set_event_bus(self, event_bus):
        """Set event_bus on self and propagate to all coders in the pool."""
        self._event_bus = event_bus
        for coder in self.coder_pool.values():
            coder._event_bus = event_bus

    def _build_coder_pool(self, model_map):
        seen_llms = {}

        for size, llm in model_map.items():
            llm_key = f"{llm.provider}:{llm.model}"

            if llm_key not in seen_llms:
                if self.use_claude_code:
                    ClaudeCodeAgent = _get_claude_code_agent()
                    coder = ClaudeCodeAgent(
                        name=f"Coder-{size}",
                        model_name=f"ollama:{llm.model}",
                        workspace_path=WORKSPACE_PATH,
                        max_turns=self.claude_code_config.max_turns,
                        timeout=self.claude_code_config.timeout,
                        allowed_tools=self.claude_code_config.allowed_tools,
                    )
                    Utils.console_print(f"  Created Coder-{size} [Claude Code] (ollama:{llm.model})", "green")
                else:
                    coder = CoderAgent(
                        name=f"Coder-{size}",
                        system_prompt=self.coder_prompt,
                        llm=llm,
                        tool_executor=self.tool_executor
                    )
                    Utils.console_print(f"  Created Coder-{size} ({llm_key})", "yellow")

                seen_llms[llm_key] = coder

            self.coder_pool[size] = seen_llms[llm_key]

        mode = "Claude Code" if self.use_claude_code else "Direct LLM"
        Utils.console_print(f"  Coder pool ready [{mode}]: {list(self.coder_pool.keys())} ({len(seen_llms)} unique)", "yellow", bold=True)

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
        # Claude Code mode: delegate entirely to ClaudeCodeAgent
        if self.use_claude_code and hasattr(coder, 'execute_task'):
            context = ""
            if workspace_context_bus and workspace_context_bus.history():
                recent = workspace_context_bus.history()[-5:]
                context = "\n".join(str(s) for s in recent)

            res_coder = coder.execute_task(
                task,
                context=context,
                retry_feedback=retry_feedback,
                sibling_context=sibling_context,
            )

            if res_coder["success"]:
                # Run validator if available
                if validator:
                    validation = validator.validate_task(task, res_coder)
                    print(f"  [VALIDATE] {validation['status']}")
                    if validation["status"] == "PASSED":
                        return True, {"task_id": task["id"], "summary": res_coder["summary"], "success": True}, None
                    else:
                        return False, res_coder, f"Validation failed: {validation.get('issues', '')}"
                return True, {"task_id": task["id"], "summary": res_coder["summary"], "success": True}, None
            else:
                return False, res_coder, res_coder.get("summary", "Claude Code execution failed")

        # Classic mode: plan_task + code_task
        res_coder = None
        feedback_history = []

        if retry_feedback:
            feedback_history.append(retry_feedback)

        for attempt in range(self.max_retries + 1):
            current_feedback = "\n".join(feedback_history) if feedback_history else None
            file_context = self._get_file_context(task)

            action_plan = coder.plan_task(task, workspace_context_bus, current_feedback, file_context, sibling_context=sibling_context)
            planned_tools = [a.get("tool") for a in action_plan.get("actions", [])]
            print(f"  [PLAN] actions: {planned_tools}")

            # Reject read-only plans when context was already provided
            has_write = any(t in planned_tools for t in ("create_file", "smart_edit"))
            if not has_write and file_context:
                print(f"  [REJECTED] Read-only plan with context already provided")
                feedback_history.append(
                    f"Attempt {attempt + 1}: You planned only {planned_tools} but context is already provided. "
                    f"You MUST use create_file or smart_edit to make changes. "
                    f"Do NOT use read_context — the file contents are already in your prompt. "
                    f"Task: {task['description'][:200]}"
                )
                if attempt < self.max_retries:
                    print(f"  [RETRY] Attempt {attempt + 2}/{self.max_retries + 1}")
                continue

            res_coder = coder.code_task(action_plan, WORKSPACE_PATH)

            if not res_coder["success"]:
                actions_used = [a.get("tool") for a in action_plan.get("actions", [])]
                smart_edit_used = "smart_edit" in actions_used
                read_used = any(a in actions_used for a in ("read_context", "read_file", "file_summary", "read_file_lines"))

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
                        # Sacar el path del primer smart_edit fallido
                        target_file = ""
                        for r in res_coder.get("results", []):
                            if r.get("tool") == "smart_edit" and not r.get("result", {}).get("success"):
                                # El path está en el error message o en el result
                                err = r["result"].get("error", "")
                                import re
                                path_match = re.search(r"in (.+)$", err)
                                if path_match:
                                    target_file = path_match.group(1)
                                    break

                        existing = ""
                        if target_file:
                            summary = file_summary(target_file, WORKSPACE_PATH)
                            if summary.get("success"):
                                s = summary["summary"]
                                existing = f" Existing classes: {[c['name'] for c in s.get('classes', [])]}. Existing functions: {[f['name'] for f in s.get('functions', [])]}."
                        new_feedback = (
                            f"smart_edit failed:{error_text} - {existing} "
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
        target = task.get("file")
        desc = task.get("description", "")
        files = list_files(".", WORKSPACE_PATH)
        if not files["success"]:
            return None

        # Extraer archivos mencionados en la descripción de la task
        mentioned = set()
        for f in files.get("files", []):
            if f in desc:
                mentioned.add(f)

        # Skip heavy/irrelevant files
        SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
                      "Cargo.lock", "poetry.lock", "Pipfile.lock", "go.sum",
                      "node_modules", ".git", ".next", "dist", "build"}

        summaries = []
        for f in files.get("files", []):
            if not f.endswith(('.py', '.js', '.ts', '.tsx', '.jsx', '.json', '.sql', '.svelte', '.vue', '.html', '.css', '.scss')):
                continue
            # Skip lock files, build artifacts, and heavy generated files
            basename = os.path.basename(f)
            if basename in SKIP_FILES or any(skip in f for skip in ("node_modules/", "dist/", ".next/", "__pycache__/")):
                continue

            is_target = target and f.endswith(target)
            is_dependency = f in mentioned

            if is_target or is_dependency:
                # Código completo para target y dependencias
                result = read_file(f, WORKSPACE_PATH)
                if result["success"]:
                    prefix = ">>> TARGET" if is_target else ">>> DEPENDENCY"
                    summaries.append(f"{prefix} {f}:\n{result['content']}")
            else:
                # Solo summary para el resto
                result = file_summary(f, WORKSPACE_PATH)
                if result["success"]:
                    summaries.append(f"    {f}:\n{result['content']}")

        if not summaries:
            return None
        return "WORKSPACE FILES:\n" + "\n".join(summaries)

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
        # Learning: suggest better model tier based on history
        original_model = task.get("model", "medium")
        if self.execution_history:
            suggested = self.execution_history.suggest_model_for_task(task, default=original_model)
            if suggested != original_model:
                print(f"\n  [LEARNED] Upgrading {task['id']} from {original_model} -> {suggested} (based on history)")
                task["model"] = suggested

        print(f"\nExecuting task {task['id']}: {task['title']} [{task.get('model', 'medium')}]")

        current_model = task.get("model", "medium")
        coder = self._get_coder(task)
        import time as _time
        _task_start_time = _time.time()
        _escalation_count = 0
        _retry_count = 0
        if coder.llm:
            print(f"  Using: {coder.name} ({coder.llm.provider}:{coder.llm.model})")
        else:
            print(f"  Using: {coder.name} ({getattr(coder, 'model_name', 'claude-code')})")
        
        # Get context for sibling tasks
        sibling_context = self._get_sibling_context(task, completed_tasks)

        # Snapshot ANTES de que nadie toque nada
        pre_task_snapshot = tempfile.mkdtemp(prefix="ws_task_")
        shutil.copytree(WORKSPACE_PATH, pre_task_snapshot, dirs_exist_ok=True)

        # First attempt with assigned model
        success, result, last_feedback = self._attempt_task(task, coder, validator, workspace_context_bus, sibling_context=sibling_context)
        if success:
            self._record_execution(task, current_model, current_model, _retry_count, 0, True, _task_start_time)
            return result

        # Escalate through higher tiers
        tier = current_model

        while tier in self.ESCALATION_CHAIN:
            next_tier = self.ESCALATION_CHAIN[tier]
            if next_tier not in self.coder_pool:
                break

            _escalation_count += 1

            # Restaurar estado limpio antes de cada tier
            shutil.rmtree(WORKSPACE_PATH)
            shutil.copytree(pre_task_snapshot, WORKSPACE_PATH)

            escalated_coder = self.coder_pool[next_tier]
            if escalated_coder.llm:
                print(f"\n  [ESCALATE] {tier} -> {next_tier} ({escalated_coder.llm.provider}:{escalated_coder.llm.model})")
            else:
                print(f"\n  [ESCALATE] {tier} -> {next_tier} ({getattr(escalated_coder, 'model_name', 'claude-code')})")

            file_context = self._get_file_context(task) or "File does not exist yet."
            escalation_feedback = (
                f"A weaker model failed this task. "
                f"Implement it completely. "
                f"Previous failure: {last_feedback}\n\n"
                f"{file_context}"
            )

            success, result, last_feedback = self._attempt_task(task, escalated_coder, validator, workspace_context_bus, escalation_feedback, sibling_context=sibling_context)
            if success:
                self._record_execution(task, current_model, next_tier, _retry_count, _escalation_count, True, _task_start_time)
                return result

            tier = next_tier

        # Restaurar workspace — como si la task nunca existió
        shutil.rmtree(WORKSPACE_PATH)
        shutil.copytree(pre_task_snapshot, WORKSPACE_PATH)
        shutil.rmtree(pre_task_snapshot)
        self._record_execution(task, current_model, tier, _retry_count, _escalation_count, False, _task_start_time)
        print(f"  [EXHAUSTED] Task {task['id']} failed — workspace restored")
        return {"task_id": task["id"], "summary": "Failed after all escalation tiers", "success": False}

    def _record_execution(self, task, assigned_model, final_model, retries, escalations, success, start_time):
        """Record task execution result for learning."""
        if not self.execution_history:
            return
        import time as _time
        from core.learning import ExecutionRecord
        self.execution_history.add(ExecutionRecord(
            task_id=task.get("id", ""),
            task_description=task.get("description", ""),
            assigned_model=assigned_model,
            final_model=final_model,
            retries=retries,
            escalations=escalations,
            success=success,
            duration_ms=(_time.time() - start_time) * 1000,
        ))

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
            if prev_fix_count is not None and current_fix_count > prev_fix_count:
                print(f"  [ABORT] Fixes increasing ({prev_fix_count} -> {current_fix_count})")
                break

            prev_fix_count = current_fix_count
            print(f"\n  [DEBUG ROUND {round_num}/{max_rounds}] ({current_fix_count} fixes)")

            debug_context_bus = MessageBus()
            for i, fix in enumerate(result["fixes"]):
                fix_task = {
                    "id": f"fix.{round_num}.{i+1}",
                    "title": f"Fix: {fix.get('method', fix.get('file', 'unknown'))}",
                    "description": fix.get("description", fix.get("issue", "Fix required")),
                    "file": fix.get("file", ""),
                    "model": "medium",
                    "group_id": f"fix.{round_num}"
                }
                print(f"  [FIX {i+1}/{current_fix_count}] {fix.get('issue', 'unknown issue')}")
                task_result = self.run_task(fix_task, debug_context_bus, [], validator)
                if not task_result.get("success"):
                    print(f"  [ERROR] Fix {fix_task['id']} failed, continuing")

            result = validator.validate_project(tasks, completed_tasks)
            print(f"  [REVALIDATE] {result['status']}")

        return result