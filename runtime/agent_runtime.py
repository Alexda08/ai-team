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

        while turn < self.max_turns:
            review = architect.review_plan(plan)
            criteria = review.get("criteria", {})
 
            if review["status"] == "VIABLE":
                print(f"[ARCHITECT] PASSED Plan is viable")
                Utils.save_text("output/plan.md", plan)
                self.state["plan"] = plan
                break

            # REWORK: Thinker revises the plan using architect's feedback
            unclear = [k for k, v in criteria.items() if v == "UNCLEAR"]
            print(f"  [REWORK] Round {turn + 1} | Unclear: {unclear}")
            plan = thinker.generate_plan(plan, feedback=review)
            turn += 1

        blueprint = architect.generate_bluePrint(plan)
        self.state["blueprint"] = blueprint
        Utils.save_text("output/blueprint.md", blueprint)

        self.state["phase"] = "tasking"

    def run_tasking(self):
        Utils.console_print("\nTasking running...\n", "red", bold=True)
        tasker = self.agents["Tasker"]
        # architect = self.agents["Architect"]

        response = tasker.generate_tasks(self.state["blueprint"])
        refined_response = tasker.refine(response)

        if not refined_response:
            print("\n[ERROR] Tasking produced no tasks. Stopping pipeline.")
            self.state["phase"] = "failed"
            return
            
        refined_response.sort(key=lambda t: t["id"])

        Utils.save_text("output/tasks.json", json.dumps(refined_response, indent=2))
        self.state["tasks"] = refined_response
        self.state["phase"] = "execution"

    def run_execution(self):
        Utils.console_print("\nExecution running...\n", "yellow", bold=True)
        executor = self.agents["Executor"]

        if self.state["completed_tasks"]:
            response = Utils.select_menu(options={"clean": "Start fresh", "continue": "Continue from last execution"}, title="Do you want to continue from the last execution?")
            
            if response == "clean":
                executor.clean_workspace()
                self.state["completed_tasks"] = []
        else:
            executor.clean_workspace()

        for task in self.state["tasks"]:
            # Skip already completed tasks
            if task["id"] in self.state["completed_tasks"]:
                print(f"\n  [SKIP] Task {task['id']}: {task['title']} (already completed)")
                # self.workspace_context_bus.publish(str(task["id"]) + " already completed")
                continue
            
            result = executor.run_task(task, self.workspace_context_bus, self.state["completed_tasks"])
            self.state["phase"] = "done"

            if result["success"]:
                self.state["completed_tasks"].append(result["task_id"])
                # now after each task, clear the workspace context bus, i only pas the context of previous task to
                # the next task, remove this clear to pass full context to each task
                self.workspace_context_bus.clear()
                self.workspace_context_bus.publish(result["summary"])
            else:
                self.state["phase"] = "failed"
                break

        Utils.save_text("output/completed_tasks.json", json.dumps(self.state["completed_tasks"], indent=2))

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

