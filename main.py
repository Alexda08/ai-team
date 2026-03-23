from llm.client import LLMClient
from agents.base_agent import BaseAgent
from agents.ThinkerAgent import ThinkerAgent
from agents.TaskerAgent import TaskerAgent
from runtime.agent_runtime import AgentRuntime
from common.utils import Utils

def main():
    # LLM
    llm = LLMClient(model="minimax-m2.7:cloud")

    # Prompts
    success, prompts = Utils.load_prompts()
    if not success:
        print(prompts)
        return

    # Agents
    init_agents = {
        "Thinker": ThinkerAgent(
            name="Thinker",
            system_prompt=prompts["thinker_prompt"],
            llm=llm
        ),
        "Critic":BaseAgent(
            name="Critic",
            system_prompt=prompts["critic_prompt"],
            llm=llm
        ),
       "Tasker":TaskerAgent(
            name="Tasker",
            system_prompt=prompts["tasker_prompt"],
            llm=llm
        ),
       "Executor":BaseAgent(
            name="Executor",
            system_prompt=prompts["executor_prompt"],
            llm=llm
        )
    }

    # Prompt inicial
    success, content = Utils.load_text("user_prompt.txt", "./")

    if not success:
        print(content)
        return

    Utils.console_print("Welcome to the AI Dev Team!\n", "White", bold=True)

    # Mode selection
    mode = Utils.select_menu(options = {
        "ideation": "Full pipeline (Thinker → Tasker → Execution)",
        "planning": "Start from Planning",
        "tasking": "Start from Tasking",
        "execution": "Start from Execution",
        "none": "Exit",
    })

    if mode == "none":
        print("Exiting...")
        return

    # Runtime
    runtime = AgentRuntime(agents=init_agents)
    runtime.state["phase"] = mode
    runtime.run(content)

if __name__ == "__main__":
    main()