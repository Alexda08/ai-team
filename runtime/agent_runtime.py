from common.utils import Utils
from runtime.runtime_helper import RuntimeHelper
from runtime.message_bus import MessageBus
import json

class AgentRuntime:
    def __init__(self, agents, max_turns=10):
        self.agents = agents
        self.message_bus = MessageBus()
        self.workspace_context_bus = MessageBus()
        self.max_turns = max_turns
        self.state = {
            "phase": "ideation",
            "plan": RuntimeHelper.get_plan(),
            "blueprint": RuntimeHelper.get_blueprint(),
            "tasks": RuntimeHelper.get_tasks(),
            "completed_tasks": RuntimeHelper.get_completed_tasks(),
            "issues": []
        }

    # Main Runtime Stages ---------------------------------------------------------------------------------
    def run_ideation(self):
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
            return

        self.state["phase"] = "planning"

    def run_planning(self):
        Utils.console_print("\nPlanning running...\n", "magenta", bold=True)
        thinker = self.agents["Thinker"]
        architect = self.agents["Architect"]
        turn = 0

        plan = thinker.generate_plan(self.message_bus.history())
        print("\nPlan generated. architect reviewing...\n")

        review = architect.review_plan(plan)
        while review["status"] != "VIABLE" and turn < self.max_turns:
            plan = thinker.generate_plan(plan, feedback=review)
            review = architect.review_plan(plan)
            print(f"  [REWORK] Round {review["status"]} | Unclear: {review['criteria']['required']}")
            turn += 1

        if turn >= self.max_turns:
            print("\nMax turns reached in planning phase.")
            self.state["phase"] = "failed"
            return

        print(f"[ARCHITECT] PASSED Plan is viable")
        Utils.save_text("output/plan.md", plan)
        self.state["plan"] = plan

        blueprint = architect.generate_bluePrint(plan)
        self.state["blueprint"] = blueprint
        Utils.save_text("output/blueprint.md", blueprint)

        self.state["phase"] = "tasking"

    def run_tasking(self):
        Utils.console_print("\nTasking running...\n", "red", bold=True)
        tasker = self.agents["Tasker"]
        architect = self.agents["Architect"]

        print("  [TASKER] Generating task tree...")
        raw_tree = tasker.generate_tasks(self.state["blueprint"], plan=self.state["plan"])
        task_tree, issues = tasker.validate(raw_tree)

        if task_tree is None:
            print(f"\n[ERROR] Tasking failed: {issues}")
            self.state["phase"] = "failed"
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
            return

        Utils.save_text("output/tasks.json", json.dumps(flat_tasks, indent=2))
        self.state["tasks"] = flat_tasks
        self.state["phase"] = "execution"

    def run_execution(self):
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
            
            result = executor.run_task(task, self.workspace_context_bus, self.state["completed_tasks"], validator)

            if result["success"]:
                self.state["completed_tasks"].append(result["task_id"])
                self.workspace_context_bus.publish(result["summary"])
            else:
                self.state["phase"] = "failed"
                Utils.save_text("output/completed_tasks.json", json.dumps(self.state["completed_tasks"], indent=2))
                return
        
        print("\n  Running final project validation...")
        final_validation = executor.run_final_validation(validator, self.state["tasks"], self.state["completed_tasks"])

        if final_validation["status"] == "FAILED":
            self.state["phase"] = "failed"
            return
        
    # Main runtime loop ----------------------------------------------------------------------------------
    def run(self, user_prompt):
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

