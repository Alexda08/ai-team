from common.utils import Utils
from runtime.runtime_helper import RuntimeHelper
from runtime.message_bus import MessageBus
from evaluation.score import ScoreSystem
from evaluation.score_bus import ScoreBus
import json

class AgentRuntime:
    def __init__(self, agents, max_turns=10):
        self.agents = agents
        self.message_bus = MessageBus()
        self.max_turns = max_turns
        self.state = {
            "phase": "ideation",
            "plan": RuntimeHelper.get_plan(),
            "tasks": RuntimeHelper.get_tasks(),
            "completed_tasks": [],
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
        plan = thinker.generate_plan(self.message_bus.history())
        Utils.save_text("output/plan.md", plan)
        self.state["plan"] = plan

        self.state["phase"] = "tasking"
        print("\nPlan has been generated.\n")

    def run_tasking(self):
        Utils.console_print("\nTasking running...\n", "red", bold=True)
        tasker = self.agents["Tasker"]

        response = tasker.generate_phases(self.state["plan"])
        print(response)

        # response = tasker.generate_tasks(self.state["plan"])
        # refined_response = tasker.refine(response)

        # if not Utils.is_valid_json(refined_response):
        #     print("\nInvalid response from refined Tasker.")
        #     self.state["phase"] = "failed"
        #     return

        # Utils.save_text("output/tasks.json", json.dumps(refined_response, indent=2))
        # self.state["tasks"] = refined_response
        self.state["phase"] = "execution"

    def run_execution(self):
        Utils.console_print("\nExecution running...\n", "yellow", bold=True)
        self.state["phase"] = "done"

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

