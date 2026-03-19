from common.utils import Utils
from runtime.runtime_helper import RuntimeHelper
from runtime.message_bus import MessageBus
from evaluation.score import ScoreSystem
from evaluation.score_bus import ScoreBus

class AgentRuntime:
    def __init__(self, agents, max_turns=10):
        self.agents = agents
        self.message_bus = MessageBus()
        self.score_bus = ScoreBus()
        self.max_turns = max_turns
        self.state = {
            "phase": "ideation",
            "plan": None,
            "tasks": [],
            "completed_tasks": []
        }

        #     "ideation",     # Thinker ↔ Critic
        #     "planning",     # generar plan.md
        #     "tasking",     # plan → tasks
        #     "execution",    # tasks → agentes
        #     "done"
        #     "failed",       # Fallo en alguno de los agentes

    # Main Runtime Stages ---------------------------------------------------------------------------------
    def run_ideation(self):
        print("\nIdeation running...\n")
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
        print("\nPlanning running...\n")
        thinker = self.agents["Thinker"]
        plan = thinker.generate_plan(self.message_bus.history())
        Utils.save_text("output/plan.md", plan)
        self.state["plan"] = plan

        self.state["phase"] = "tasking"
        print("\nPlan has been generated. Generating tasks...\n")

    def run_tasking(self):
        print("\nTasking running...\n")
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
        
        if self.state["phase"] == "failed":
            print("\nRuntime failed.\n")
        elif self.state["phase"] == "done":
            print("\nRuntime completed.\n")

