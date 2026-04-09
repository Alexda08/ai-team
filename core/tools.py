from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import time
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout


@dataclass
class ToolSpec:
    """Metadata describing a tool's interface and constraints."""
    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    category: str = "general"
    cost_estimate: str = "low"       # low / medium / high
    timeout: int = 60                # seconds
    required_capabilities: List[str] = field(default_factory=list)
    requires_confirmation: bool = False

    def to_openai_function(self) -> Dict[str, Any]:
        """Convert to OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }


class BaseTool(ABC):
    """Abstract base for all tools."""

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        ...

    @abstractmethod
    def execute(self, **kwargs: Any) -> Dict[str, Any]:
        ...


class ToolExecutor:
    """Dispatches tool execution with event emission, timeout, and optional security checks.

    Security (capability_policy, boundary_guard) are injected in Phase 5.
    """

    def __init__(
        self,
        tools: Optional[List[BaseTool]] = None,
        event_bus=None,
        capability_policy=None,
        boundary_guard=None,
        default_timeout: float = 60.0,
    ):
        self._tools: Dict[str, BaseTool] = {}
        if tools:
            for tool in tools:
                self._tools[tool.spec.name] = tool
        self._event_bus = event_bus
        self._capability_policy = capability_policy
        self._boundary_guard = boundary_guard
        self._default_timeout = default_timeout

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.spec.name] = tool

    def execute(self, tool_name: str, agent_name: str = "", **kwargs: Any) -> Dict[str, Any]:
        """Execute a tool by name with safety checks and event emission."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

        # Phase 5: Capability check (no-op until security is wired)
        if self._capability_policy:
            for cap_name in tool.spec.required_capabilities:
                if not self._capability_policy.check(agent_name, cap_name):
                    if self._event_bus:
                        from core.events import EventType
                        self._event_bus.publish(EventType.CAPABILITY_DENIED, {
                            "agent": agent_name, "tool": tool_name, "capability": cap_name
                        })
                    return {"success": False, "error": f"Capability '{cap_name}' denied for '{agent_name}'"}

        # Phase 5: Boundary guard (no-op until security is wired)
        if self._boundary_guard and "content" in kwargs:
            issues = self._boundary_guard.scan_outbound(kwargs["content"], {
                "agent": agent_name, "tool": tool_name
            })
            if issues:
                if self._event_bus:
                    from core.events import EventType
                    self._event_bus.publish(EventType.BOUNDARY_BLOCKED, {
                        "agent": agent_name, "tool": tool_name, "issues": issues
                    })
                return {"success": False, "error": f"Content blocked: {'; '.join(issues)}"}

        # Emit start event
        if self._event_bus:
            from core.events import EventType
            self._event_bus.publish(EventType.TOOL_CALL_START, {
                "tool": tool_name, "agent": agent_name,
                "args_summary": str(kwargs)[:200],
            })

        # Execute with timeout
        timeout = tool.spec.timeout or self._default_timeout
        start = time.time()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(tool.execute, **kwargs)
                result = future.result(timeout=timeout)
        except FuturesTimeout:
            result = {"success": False, "error": f"Tool '{tool_name}' timed out after {timeout}s"}
        except Exception as e:
            result = {"success": False, "error": f"Tool '{tool_name}' error: {e}"}
        finally:
            duration_ms = (time.time() - start) * 1000

        # Emit end event
        if self._event_bus:
            self._event_bus.publish(EventType.TOOL_CALL_END, {
                "tool": tool_name, "agent": agent_name,
                "success": result.get("success", False),
                "duration_ms": duration_ms,
            })

        return result

    def available_tools(self) -> List[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        return [tool.spec.to_openai_function() for tool in self._tools.values()]

    def tool_names(self) -> List[str]:
        return list(self._tools.keys())


def build_tool_descriptions(tools: List[BaseTool], include_category: bool = True) -> str:
    """Build markdown tool descriptions for system prompts."""
    lines = []
    for tool in tools:
        s = tool.spec
        header = f"**{s.name}**"
        if include_category and s.category:
            header += f" [{s.category}]"
        lines.append(header)
        lines.append(f"  {s.description}")
        if s.parameters.get("properties"):
            for param, info in s.parameters["properties"].items():
                required = param in s.parameters.get("required", [])
                req_marker = " (required)" if required else ""
                lines.append(f"  - `{param}`{req_marker}: {info.get('description', info.get('type', ''))}")
        lines.append("")
    return "\n".join(lines)
