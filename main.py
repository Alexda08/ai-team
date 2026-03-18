from llm.client import LLMClient
from agents.base_agent import BaseAgent
from agents.ThinkerAgent import ThinkerAgent
from runtime.agent_runtime import AgentRuntime
from runtime.message_bus import MessageBus
from prompts.prompt_loader import load_prompt


def main():

    # LLM
    llm = LLMClient(model="minimax-m2.5:cloud")

    # Agents
    thinker = ThinkerAgent(
        name="Thinker",
        system_prompt=load_prompt("thinker_prompt.txt"),
        llm=llm
    )

    critic = BaseAgent(
        name="Critic",
        system_prompt=load_prompt("critic_prompt.txt"),
        llm=llm
    )

    # Message Bus
    bus = MessageBus()

    # Runtime
    runtime = AgentRuntime(
        agents={
            "Thinker": thinker,
            "Critic": critic
        },
        message_bus=bus,
        max_turns=10
    )

    # Prompt inicial
    prompt = "tarea: crear un sistema de gestión de inventarios para una empresa de tecnología"

    runtime.run(prompt)


if __name__ == "__main__":
    main()