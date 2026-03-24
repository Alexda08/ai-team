import os, json
from agents.base_agent import BaseAgent

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_PATH = os.path.join(BASE_DIR, "workspace")

class ExecutorAgent (BaseAgent):

    def __init__(self, name, system_prompt, llm):
        super().__init__(name, system_prompt, llm)
        os.makedirs(WORKSPACE_PATH, exist_ok=True)

    def run_task(self, task, coder, workspace_context_bus, completed_tasks):
        print(f"Executing task {task['id']}: {task['title']}")
        action_plan = coder.plan_task(task, workspace_context_bus)
        res_coder = coder.code_task(action_plan, WORKSPACE_PATH)
        
        print("action_plan", action_plan)
        
        return {
            "task_id": task["id"],
            "summary": res_coder["summary"],
            "success": res_coder["success"]
        }
