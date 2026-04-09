import sys
import os

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
            claude_code_config=config.claude_code,
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
        "ideation": "Full pipeline (Thinker -> Tasker -> Execution)",
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

    # === WIRE UP INFRASTRUCTURE ===

    # 1. Inject EventBus into all agents (+ propagate to Coders in pool)
    for agent in init_agents.values():
        if hasattr(agent, 'set_event_bus'):
            agent.set_event_bus(runtime.event_bus)
        else:
            agent._event_bus = runtime.event_bus

    # 2. Create ToolExecutor with all registered tools
    from core.tools import ToolExecutor
    from core.registry import ToolRegistry
    import common.tools.wrapped_tools   # registers create_file, read_context, smart_edit, etc.
    import common.tools.env_tools       # registers shell, filesystem, process, http, git, package_manager

    tool_executor = ToolExecutor(
        tools=[cls() for _, cls in ToolRegistry.items()],
        event_bus=runtime.event_bus,
    )

    # 3. Security: if enabled, wire capability policy + boundary guard
    if config.security.enabled:
        from core.security import PolicyManager, BoundaryGuard
        policy_mgr = PolicyManager()
        policy_mgr.load_defaults()
        boundary = BoundaryGuard(mode="warn", event_bus=runtime.event_bus)
        tool_executor._capability_policy = policy_mgr
        tool_executor._boundary_guard = boundary
        Utils.console_print("  Security enabled (RBAC + BoundaryGuard)", "green")

    # 4. Inject ToolExecutor into Executor (which passes it to Coders)
    init_agents["Executor"].tool_executor = tool_executor

    # 5. Inject LoopGuard into Executor
    init_agents["Executor"].loop_guard = runtime.loop_guard

    # 6. Learning: create ExecutionHistory and inject into Executor
    execution_history = None
    if config.learning.enabled:
        from core.learning import ExecutionHistory
        execution_history = ExecutionHistory(
            path=os.path.join(config.learning.config_dir, "execution_history.json")
        )
        init_agents["Executor"].execution_history = execution_history

        # Show what we've learned from previous runs
        if execution_history.total_records > 0:
            recs = execution_history.get_model_recommendations()
            Utils.console_print(
                f"  Learning: {execution_history.total_records} records, "
                f"success rate {execution_history.success_rate:.0%}", "green"
            )
            if recs:
                for pattern, tier in recs.items():
                    Utils.console_print(f"    {pattern} -> start with {tier}", "green")

    # Print infrastructure status
    features = []
    if runtime.trace_collector:
        features.append("Traces")
    features.append("LoopGuard")
    features.append(f"ToolExecutor ({len(tool_executor.tool_names())} tools)")
    if config.security.enabled:
        features.append("Security")
    if config.learning.enabled:
        features.append(f"Learning ({execution_history.total_records if execution_history else 0} records)")
    Utils.console_print(f"  Infrastructure: {', '.join(features)}\n", "cyan")

    runtime.run(content)

if __name__ == "__main__":
    main()
