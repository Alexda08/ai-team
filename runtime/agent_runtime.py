from common.utils import Utils
from runtime.runtime_helper import RuntimeHelper
from runtime.message_bus import MessageBus
from core.events import EventBus, EventType
from core.loop_guard import LoopGuard, LoopGuardConfig
import json

class AgentRuntime:
    def __init__(self, agents, max_turns=10, config=None):
        self.agents = agents
        self.config = config
        self.event_bus = EventBus(record_history=True)
        self.message_bus = MessageBus(event_bus=self.event_bus)
        self.workspace_context_bus = MessageBus(event_bus=self.event_bus)
        self.max_turns = max_turns if config is None else config.runtime.max_turns

        # LoopGuard — always active
        self.loop_guard = LoopGuard(LoopGuardConfig(
            max_identical_calls=3,
            ping_pong_window=6,
            max_context_messages=self.max_turns * 3,
        ))

        # TraceCollector — only if config enables it
        self.trace_store = None
        self.trace_collector = None
        if config and config.traces.enabled:
            from core.traces import TraceStore, TraceCollector
            self.trace_store = TraceStore(config.traces.db_path)
            self.trace_collector = TraceCollector(self.event_bus, self.trace_store)

        self.state = {
            "phase": "ideation",
            "ideation": RuntimeHelper.get_ideation(),
            "plan": RuntimeHelper.get_plan(),
            "blueprint": RuntimeHelper.get_blueprint(),
            "tasks": RuntimeHelper.get_tasks(),
            "completed_tasks": RuntimeHelper.get_completed_tasks(),
            "issues": []
        }

    def _emit_phase(self, event_type, phase, **extra):
        self.event_bus.publish(event_type, {"phase": phase, **extra})

    # Main Runtime Stages ---------------------------------------------------------------------------------
    def run_ideation(self):
        self._emit_phase(EventType.PHASE_START, "ideation")
        Utils.console_print("\nIdeation running...\n", "cyan", bold=True)
        turn = 0
        approved = False

        thinker = self.agents["Thinker"]
        critic = self.agents["Critic"]

        while turn < self.max_turns:
            # THINKER
            t_resp = thinker.run(self.message_bus.history())
            self.message_bus.publish(t_resp)

            print("\n--------------------------------------------------")
            print(f"{thinker.name}: {t_resp['content']}")

            # LoopGuard: check if Thinker is repeating itself
            verdict = self.loop_guard.check_agent_turn("Thinker", t_resp.get("content", ""))
            if verdict.blocked:
                Utils.console_print(f"\n[LOOP GUARD] {verdict.reason}", "red", bold=True)
                self.state["phase"] = "failed"
                self._emit_phase(EventType.PHASE_END, "ideation", status="loop_detected")
                return

            # CRITIC
            c_resp = critic.run(self.message_bus.history())
            self.message_bus.publish(c_resp, {"status": RuntimeHelper.get_status(c_resp), "iteration": turn})

            print("\n--------------------------------------------------")
            print(f"{critic.name}: {c_resp['content']}")

            if approved := RuntimeHelper.is_approved(c_resp):
                break
            turn += 1

        if turn >= self.max_turns and not approved:
            print("\nMax turns reached in ideation phase.")
            self.state["phase"] = "failed"
            self._emit_phase(EventType.PHASE_END, "ideation", status="failed")
            return

        self.state["ideation"] = self.message_bus.history()
        Utils.save_text("output/ideation.md",  json.dumps(self.message_bus.history(), indent=2))
        self.state["phase"] = "planning"
        self._emit_phase(EventType.PHASE_END, "ideation", status="completed")

    def run_planning(self):
        self._emit_phase(EventType.PHASE_START, "planning")
        Utils.console_print("\nPlanning running...\n", "magenta", bold=True)
        thinker = self.agents["Thinker"]
        architect = self.agents["Architect"]
        turn = 0

        if not (ideation := self.state["ideation"]):
            print("\n[WARN] Can't generate plan without ideation. Returning to ideation phase.")
            self.state["phase"] = "ideation"
            self._emit_phase(EventType.PHASE_END, "planning", status="skipped")
            return

        plan = thinker.generate_plan(ideation)
        print("\nPlan generated. architect reviewing...\n")

        review = architect.review_plan(plan)
        while review["status"] != "VIABLE" and turn < self.max_turns and turn >= 1:
            # LoopGuard: check if plan is repeating
            verdict = self.loop_guard.check_agent_turn("Thinker_plan", plan[:500])
            if verdict.blocked:
                Utils.console_print(f"\n[LOOP GUARD] {verdict.reason}", "red", bold=True)
                break

            plan = thinker.generate_plan(plan, feedback=review)
            review = architect.review_plan(plan)
            print(f"  [REWORK] Round {review["status"]} | Unclear: {review['criteria']['required']}")
            turn += 1

        if turn >= self.max_turns:
            print("\nMax turns reached in planning phase.")
            self.state["phase"] = "failed"
            self._emit_phase(EventType.PHASE_END, "planning", status="failed")
            return

        print(f"[ARCHITECT] PASSED Plan is viable")
        Utils.save_text("output/plan.md", plan)
        self.state["plan"] = plan

        print("\n[ARCHITECT] Generating blueprint...")
        blueprint = architect.generate_bluePrint(plan)
        self.state["blueprint"] = blueprint
        Utils.save_text("output/blueprint.md", blueprint)

        self.state["phase"] = "tasking"
        self._emit_phase(EventType.PHASE_END, "planning", status="completed")

    def run_tasking(self):
        self._emit_phase(EventType.PHASE_START, "tasking")
        Utils.console_print("\nTasking running...\n", "red", bold=True)
        tasker = self.agents["Tasker"]
        architect = self.agents["Architect"]

        print("  [TASKER] Generating task tree...")
        raw_tree = tasker.generate_tasks(self.state["blueprint"], plan=self.state["plan"])
        task_tree, issues = tasker.validate(raw_tree)

        if task_tree is None:
            print(f"\n[ERROR] Tasking failed: {issues}")
            self.state["phase"] = "failed"
            self._emit_phase(EventType.PHASE_END, "tasking", status="failed")
            return

        if issues:
            print(f"  [VALIDATE] {len(issues)} issues found:")
            for issue in issues[:10]:
                print(f"    - {issue}")

        # Save tree structure
        Utils.save_text("output/tasks_tree.json", json.dumps(task_tree, indent=2))

        # Flatten for execution
        if not (flat_tasks := tasker.flatten(task_tree)):
            print("\n[ERROR] Tasking produced no tasks. Stopping pipeline.")
            self.state["phase"] = "failed"
            self._emit_phase(EventType.PHASE_END, "tasking", status="failed")
            return

        Utils.save_text("output/tasks.json", json.dumps(flat_tasks, indent=2))
        self.state["tasks"] = flat_tasks
        self.state["phase"] = "execution"
        self._emit_phase(EventType.PHASE_END, "tasking", status="completed")

    def run_execution(self):
        self._emit_phase(EventType.PHASE_START, "execution")
        Utils.console_print("\nExecution running...\n", "yellow", bold=True)
        executor = self.agents["Executor"]
        validator = self.agents["Validator"]

        executor.all_tasks = self.state["tasks"]

        if self.state["completed_tasks"]:
            response = Utils.select_menu(options={"continue": "Continue from last execution", "clean": "Start fresh"}, title="Do you want to continue from the last execution?")

            if response == "clean":
                executor.clean_workspace()
                self.state["completed_tasks"] = []
        else:
            executor.clean_workspace()

        self.state["phase"] = "done"
        for task in self.state["tasks"]:
            # Skip already completed tasks
            if task["id"] in self.state["completed_tasks"]:
                print(f"\n  [SKIP] Task {task['id']}: {task['title']} (already completed)")
                self.workspace_context_bus.publish("Task: "+ str(task["id"])+"->"+ task["description"] + " already completed")
                continue

            # Reset loop guard per task
            self.loop_guard.reset()

            self.event_bus.publish(EventType.TASK_START, {"task_id": task["id"], "title": task.get("title", "")})

            result = executor.run_task(task, self.workspace_context_bus, self.state["completed_tasks"], validator)

            if result["success"]:
                self.state["completed_tasks"].append(result["task_id"])
                self.workspace_context_bus.publish(result["summary"])
                self.event_bus.publish(EventType.TASK_END, {"task_id": task["id"], "success": True})
            else:
                self.state["phase"] = "failed"
                self.event_bus.publish(EventType.TASK_END, {"task_id": task["id"], "success": False})
                Utils.save_text("output/completed_tasks.json", json.dumps(self.state["completed_tasks"], indent=2))
                self._emit_phase(EventType.PHASE_END, "execution", status="failed")
                return

        # saving this bc it's here all tasks are done
        Utils.save_text("output/completed_tasks.json", json.dumps(self.state["completed_tasks"], indent=2))

        print("\n  Running final project validation...")
        final_validation = executor.run_final_validation(validator, self.state["tasks"], self.state["completed_tasks"])

        if final_validation["status"] == "FAILED":
            self.state["phase"] = "failed"
            self._emit_phase(EventType.PHASE_END, "execution", status="failed")
            return

        self._emit_phase(EventType.PHASE_END, "execution", status="completed")

    # Main runtime loop ----------------------------------------------------------------------------------
    def run(self, user_prompt):
        # Start trace if enabled
        if self.trace_collector:
            self.trace_collector.start_trace(query=user_prompt[:500])

        self.message_bus.publish({
            "role": "user",
            "content": user_prompt
        })

        while self.state["phase"] != "done" and self.state["phase"] != "failed":
            if self.state["phase"] == "ideation":
                self.run_ideation()
            elif self.state["phase"] == "planning":
                self.run_planning()
            elif self.state["phase"] == "tasking":
                self.run_tasking()
            elif self.state["phase"] == "execution":
                self.run_execution()

        if self.state["phase"] == "failed":
            print("\nRuntime failed.\n")
        elif self.state["phase"] == "done":
            print("\nRuntime completed.\n")

        # End trace
        if self.trace_collector:
            outcome = "success" if self.state["phase"] == "done" else "failure"
            trace = self.trace_collector.end_trace(status=self.state["phase"], outcome=outcome)
            if trace:
                print(f"  [TRACE] Saved trace {trace.trace_id} ({len(trace.steps)} steps)")

        # Learning cycle post-run
        if (self.state["phase"] == "done"
                and self.config and self.config.learning.enabled
                and self.trace_store):
            try:
                from core.learning import LearningOrchestrator
                orch = LearningOrchestrator(
                    self.trace_store,
                    config_dir=self.config.learning.config_dir,
                    min_quality=self.config.learning.min_quality,
                )
                report = orch.run_learning_cycle()
                print(f"  [LEARNING] {report['status']}: {report['sft_pairs']} pairs, {len(report['configs_updated'])} configs updated")
            except Exception as e:
                print(f"  [LEARNING] Error: {e}")
