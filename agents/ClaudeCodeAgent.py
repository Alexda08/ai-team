"""ClaudeCodeAgent — executes tasks using Claude Code as a subprocess.

Instead of LLM.generate() → JSON parse → smart_edit/create_file,
this agent delegates to Claude Code which handles file editing,
command execution, verification, and self-correction internally.

Usage:
    agent = ClaudeCodeAgent("Coder-heavy", model_name="ollama:qwen3-coder-next:cloud", workspace_path="./workspace")
    result = agent.execute_task(task, context="previous task summaries...")
"""

import subprocess
import json
import os
import time

from agents.base_agent import BaseAgent


class ClaudeCodeAgent(BaseAgent):

    def __init__(self, name, model_name, workspace_path="./workspace",
                 max_turns=10, timeout=300, allowed_tools="Edit,Write,Read,Bash",
                 event_bus=None):
        # BaseAgent needs (name, system_prompt, llm) — we pass dummies since we don't use them
        super().__init__(name, system_prompt="", llm=None, event_bus=event_bus)
        self.model_name = model_name
        self.workspace_path = os.path.realpath(workspace_path)
        self.max_turns = max_turns
        self.timeout = timeout
        self.allowed_tools = allowed_tools

    def execute_task(self, task, context="", retry_feedback=None, sibling_context=None):
        """Execute a task using Claude Code as subprocess.

        Returns dict compatible with ExecutorAgent expectations:
            {"summary": str, "results": list, "success": bool}
        """
        self._emit_turn_start(f"Task {task.get('id', '')} via Claude Code")

        prompt = self._build_prompt(task, context, retry_feedback, sibling_context)

        start_time = time.time()
        try:
            # Use "ollama launch claude" which properly configures Claude Code with Ollama models
            # Format: ollama launch claude --model MODEL -y -- -p "prompt" --extra-flags
            model_bare = self.model_name.replace("ollama:", "")  # strip prefix if present
            cmd = (
                f'ollama launch claude --model {model_bare} -y '
                f'-- -p {json.dumps(prompt)} '
                f'--output-format json '
                f'--max-turns {self.max_turns} '
                f'--allowedTools "{self.allowed_tools}"'
            )

            result = subprocess.run(
                cmd,
                cwd=self.workspace_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=True,
            )

            duration_ms = (time.time() - start_time) * 1000

            # Parse JSON output (even on non-zero exit, ollama launch may output JSON)
            full_output = result.stdout or ""
            if result.stderr:
                full_output = full_output or result.stderr

            output = self._parse_output(full_output)

            response_text = output.get("result", "")
            cost_usd = output.get("total_cost_usd", 0)
            turns = output.get("num_turns", 0)
            is_error = output.get("is_error", result.returncode != 0)
            permission_denials = output.get("permission_denials", [])

            if permission_denials:
                denied_tools = [d.get("tool_name", "?") for d in permission_denials]
                print(f"  [CLAUDE CODE] Permission denied for: {denied_tools}")

            if is_error:
                print(f"  [CLAUDE CODE] Failed ({turns} turns, {duration_ms:.0f}ms): {response_text[:200]}")
                self._emit_turn_end(output_summary=f"Failed: {response_text[:100]}")
                return {
                    "summary": f"Claude Code failed: {response_text[:300]}",
                    "results": [],
                    "success": False,
                    "duration_ms": duration_ms,
                }

            print(f"  [CLAUDE CODE] Done ({turns} turns, {duration_ms:.0f}ms, ${cost_usd:.4f})")

            self._emit_turn_end(output_summary=f"success, {turns} turns, {duration_ms:.0f}ms")

            return {
                "summary": response_text[:500] if response_text else task.get("title", "Task completed"),
                "results": [],
                "success": True,
                "duration_ms": duration_ms,
                "turns": turns,
                "cost_usd": cost_usd,
            }

        except subprocess.TimeoutExpired:
            duration_ms = (time.time() - start_time) * 1000
            print(f"  [CLAUDE CODE] Timed out after {self.timeout}s")
            self._emit_turn_end(output_summary=f"Timeout after {self.timeout}s")
            return {
                "summary": f"Claude Code timed out after {self.timeout}s",
                "results": [],
                "success": False,
                "duration_ms": duration_ms,
            }

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            print(f"  [CLAUDE CODE] Error: {e}")
            self._emit_turn_end(output_summary=f"Error: {e}")
            return {
                "summary": f"Claude Code error: {e}",
                "results": [],
                "success": False,
                "duration_ms": duration_ms,
            }

    def _build_prompt(self, task, context="", retry_feedback=None, sibling_context=None):
        """Build the prompt for Claude Code."""
        parts = [
            "Implement this task in the current workspace directory.",
            "",
            f"Task: {task.get('title', '')}",
            f"Description: {task.get('description', '')}",
        ]

        if task.get("file"):
            parts.append(f"Target file: {task['file']}")

        if sibling_context:
            parts.append(f"\nCompleted related tasks:\n{sibling_context}")

        if context:
            parts.append(f"\nWorkspace context:\n{context}")

        if retry_feedback:
            parts.append(f"\nPrevious attempt failed:\n{retry_feedback}")
            parts.append("Fix the issue and try again.")

        parts.extend([
            "",
            "Rules:",
            "- Only modify files in the current directory",
            "- Do not install dependencies (they will be installed separately)",
            "- Do not start any servers or run the project",
            "- Focus only on implementing this specific task",
            "- If the target file already exists, read it first before making changes",
            "- Make sure all imports reference files that exist in the workspace",
        ])

        return "\n".join(parts)

    def _parse_output(self, stdout):
        """Parse Claude Code JSON output.

        ollama launch claude outputs "Launching Claude Code with MODEL..."
        on the first line, then JSON on subsequent lines.
        """
        if not stdout or not stdout.strip():
            return {}

        # Try parsing the entire output as JSON first
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            pass

        # Find the JSON object in the output (skip "Launching..." preamble)
        for line in stdout.strip().split("\n"):
            line = line.strip()
            if line.startswith("{") and '"type"' in line:
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue

        return {"result": stdout[:1000]}

    # Compatibility methods — ExecutorAgent may call these
    def plan_task(self, task, workspace_context_bus=None, retry_feedback=None,
                  file_context=None, sibling_context=None):
        """Compatibility: returns a dummy plan since Claude Code handles everything."""
        return {
            "reasoning": "Delegated to Claude Code",
            "actions": [{"tool": "claude_code", "path": task.get("file", "")}],
            "summary": task.get("title", ""),
        }

    def code_task(self, plan, workspace_path):
        """Compatibility: not used when Claude Code is the executor."""
        return {
            "summary": "Executed via Claude Code",
            "results": [],
            "success": True,
        }
