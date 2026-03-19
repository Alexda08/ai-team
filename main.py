from llm.client import LLMClient
from agents.base_agent import BaseAgent
from agents.ThinkerAgent import ThinkerAgent
from agents.TaskerAgent import TaskerAgent
from runtime.agent_runtime import AgentRuntime
from common.utils import Utils
from pprint import pprint

def main():
    # LLM
    llm = LLMClient(model="minimax-m2.7:cloud")

    # Prompts
    success, prompts = Utils.load_prompts()
    if not success:
        print(prompts)
        return

    # Agents
    thinker = ThinkerAgent(
        name="Thinker",
        system_prompt=prompts["thinker_prompt"],
        llm=llm
    )

    critic = BaseAgent(
        name="Critic",
        system_prompt=prompts["critic_prompt"],
        llm=llm
    )

    tasker = TaskerAgent(
        name="Tasker",
        system_prompt=prompts["tasker_prompt"],
        llm=llm
    )

    # Runtime
    runtime = AgentRuntime(
        agents={
            "Thinker": thinker,
            "Critic": critic,
            "Tasker": tasker
        }
        # max_turns=10
    )

    # Prompt inicial
    success, content = Utils.load_text("user_prompt.txt", "./")

    if not success:
        print(content)
        return

    runtime.run(content)

if __name__ == "__main__":
    main()