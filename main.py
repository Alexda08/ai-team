import sys

from llm.client import LLMClient
from agents.base_agent import BaseAgent
from agents.ThinkerAgent import ThinkerAgent
from agents.TaskerAgent import TaskerAgent
from agents.ExecutorAgent import ExecutorAgent
from agents.CoderAgent import CoderAgent
from agents.ValidatorAgent import ValidatorAgent
from agents.ArchitectAgent import ArchitectAgent
from runtime.agent_runtime import AgentRuntime
from common.utils import Utils
from core.config import load_config


def main():
    # Load config (from --config arg, DEVTEAM_CONFIG env, ./config.toml, or defaults)
    config_path = None
    if "--config" in sys.argv:
        idx = sys.argv.index("--config")
        if idx + 1 < len(sys.argv):
            config_path = sys.argv[idx + 1]

    config = load_config(config_path)

    # LLM (from config)
    llm = LLMClient.from_config(config.llm)

    # Prompts
    success, prompts = Utils.load_prompts()
    if not success:
        print(prompts)
        return

    # Model map (from config)
    model_map = LLMClient.model_map_from_config(config.llm)

    # Agents
    init_agents = {
        "Thinker": ThinkerAgent(
            name="Thinker",
            system_prompt=prompts["thinker_prompt"],
            llm=llm
        ),
        "Critic": BaseAgent(
            name="Critic",
            system_prompt=prompts["critic_prompt"],
            llm=llm
        ),
        "Tasker": TaskerAgent(
            name="Tasker",
            system_prompt=prompts["tasker_prompt"],
            llm=llm
        ),
        "Architect": ArchitectAgent(
            name="Architect",
            system_prompt=prompts["architect_prompt"],
            llm=llm
        ),
        "Executor": ExecutorAgent(
            name="Executor",
            system_prompt=prompts["executor_prompt"],
            llm=llm,
            model_map=model_map,
            coder_prompt=prompts["coder_prompt"],
        ),
        "Validator": ValidatorAgent(
            name="Validator",
            system_prompt=prompts["validator_prompt"],
            llm=llm
        )
    }

    # User prompt
    success, content = Utils.load_text("user_prompt.txt", "./")

    if not success:
        print(content)
        return

    Utils.console_print("Welcome to the AI Dev Team!\n", "White", bold=True)

    # Mode selection
    mode = Utils.select_menu(options={
        "ideation": "Full pipeline (Thinker → Tasker → Execution)",
        "planning": "Start from Planning",
        "tasking": "Start from Tasking",
        "execution": "Start from Execution",
        "none": "Exit"
    })

    if mode == "none":
        print("Exiting...")
        return

    # Runtime
    runtime = AgentRuntime(agents=init_agents, config=config)
    runtime.state["phase"] = mode
    runtime.run(content)

if __name__ == "__main__":
    main()
