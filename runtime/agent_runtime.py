from common.utils import Utils

class AgentRuntime:
    def __init__(self, agents, message_bus, max_turns=10):
        self.agents = agents
        self.bus = message_bus
        self.max_turns = max_turns
        self.state = {
            "phase": "ideation",
            "plan": None,
            "tasks": [],
            "completed_tasks": []
        }

        # PHASES = [
        #     "ideation",     # Thinker ↔ Critic
        #     "planning",     # generar plan.md
        #     "task_gen",     # plan → tasks
        #     "execution",    # tasks → agentes
        #     "done"
        # ]

    # Helpers -----------------------------------------------------------------------------------------------

    def is_approved(self, response):
        content = response["content"].upper()
        return "APPROVED" in content

    # Main Runtime Stages ---------------------------------------------------------------------------------
    def run_ideation(self):
        turn = 0
        approved = False

        thinker = self.agents["Thinker"]
        critic = self.agents["Critic"]
        
        while turn < self.max_turns and not approved:
            # THINKER
            t_resp = thinker.run(self.bus.history())
            self.bus.publish(t_resp)

            print("\n--------------------------------------------------")
            print(f"{thinker.name}: {t_resp['content']}")

            # CRITIC
            c_resp = critic.run(self.bus.history())
            self.bus.publish(c_resp)

            print("\n--------------------------------------------------")
            print(f"{critic.name}: {c_resp['content']}")
            
            if turn >= 1 and self.is_approved(c_resp):
                print("\nIdea approved.\n")
                approved = True
            turn += 1
        
        if turn >= self.max_turns and not approved:
            print("\nMax turns reached in ideation phase.")
        if approved:
            plan = thinker.generate_plan(self.bus.history())
            Utils.save_text("output/plan.md", plan)
            self.state["plan"] = plan
            self.state["phase"] = "planning"

            print("Plan saved to output/plan.md")
    
    def run_planning(self):
        print("\nPlan has been generated. Generating tasks...\n")
        self.state["phase"] = "done"

    # Main runtime loop ----------------------------------------------------------------------------------
    def run(self, user_prompt):
        
        self.bus.publish({
            "role": "user",
            "content": user_prompt
        })

        while self.state["phase"] != "done":
            if self.state["phase"] == "ideation":
                self.run_ideation()
            elif self.state["phase"] == "planning":
                self.run_planning()
        
       

