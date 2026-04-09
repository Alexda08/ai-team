"""Loop detection and context compression for agent execution.

Prevents infinite loops caused by:
- Identical tool calls (same name + args repeated)
- Ping-pong patterns (A-B-A-B alternating)
- Identical agent outputs

Also provides context compression to prevent context window overflow.
"""

from dataclasses import dataclass, field
from collections import deque
from typing import Dict, List, Optional
import hashlib
import json


@dataclass
class LoopGuardConfig:
    max_identical_calls: int = 3
    ping_pong_window: int = 6
    poll_tool_budget: int = 5
    max_context_messages: int = 100
    warn_before_block: bool = True


@dataclass
class LoopVerdict:
    blocked: bool = False
    reason: str = ""
    warned: bool = False


class LoopGuard:
    """Detects and prevents agent execution loops."""

    def __init__(self, config: Optional[LoopGuardConfig] = None, event_bus=None):
        self._config = config or LoopGuardConfig()
        self._event_bus = event_bus

        # Identical call tracking (SHA-256 hash -> count)
        self._call_counts: Dict[str, int] = {}

        # Tool sequence for ping-pong detection
        self._tool_sequence: deque = deque(maxlen=self._config.ping_pong_window * 2)

        # Per-tool budgets
        self._per_tool_counts: Dict[str, int] = {}

        # Agent output hashes for identical output detection
        self._agent_output_hashes: Dict[str, List[str]] = {}

        # Warned cycles (to implement warn-before-block)
        self._warned_hashes: set = set()

    def _hash(self, *parts: str) -> str:
        h = hashlib.sha256()
        for p in parts:
            h.update(p.encode("utf-8"))
        return h.hexdigest()[:16]

    def check_call(self, tool_name: str, arguments: dict) -> LoopVerdict:
        """Check if a tool call should be allowed or blocked."""
        # 1. Identical call detection
        call_hash = self._hash(tool_name, json.dumps(arguments, sort_keys=True))
        self._call_counts[call_hash] = self._call_counts.get(call_hash, 0) + 1
        count = self._call_counts[call_hash]

        if count > self._config.max_identical_calls:
            if self._config.warn_before_block and call_hash not in self._warned_hashes:
                self._warned_hashes.add(call_hash)
                return LoopVerdict(blocked=False, warned=True,
                                   reason=f"Tool '{tool_name}' called {count} times with same args (warning)")
            return LoopVerdict(blocked=True,
                               reason=f"Tool '{tool_name}' called {count} times with identical arguments — loop detected")

        # 2. Ping-pong detection
        self._tool_sequence.append(tool_name)
        if self._is_ping_pong():
            return LoopVerdict(blocked=True,
                               reason=f"Ping-pong pattern detected in tool sequence: {list(self._tool_sequence)[-6:]}")

        # 3. Per-tool budget
        self._per_tool_counts[tool_name] = self._per_tool_counts.get(tool_name, 0) + 1
        if self._per_tool_counts[tool_name] > self._config.poll_tool_budget:
            return LoopVerdict(blocked=True,
                               reason=f"Tool '{tool_name}' exceeded budget ({self._config.poll_tool_budget} calls)")

        return LoopVerdict()

    def check_agent_turn(self, agent_name: str, output: str) -> LoopVerdict:
        """Check if an agent is producing identical outputs."""
        output_hash = self._hash(output)

        if agent_name not in self._agent_output_hashes:
            self._agent_output_hashes[agent_name] = []

        history = self._agent_output_hashes[agent_name]
        history.append(output_hash)

        # Check last N outputs for repetition
        if len(history) >= 3:
            last_3 = history[-3:]
            if len(set(last_3)) == 1:
                return LoopVerdict(blocked=True,
                                   reason=f"Agent '{agent_name}' produced 3 identical outputs — loop detected")

        # Keep only last 10
        if len(history) > 10:
            self._agent_output_hashes[agent_name] = history[-10:]

        return LoopVerdict()

    def compress_context(self, messages: List[dict]) -> List[dict]:
        """Compress conversation context to prevent overflow.

        4-stage compression:
        1. Truncate old tool results to summaries
        2. Sliding window: keep system + recent messages
        3. Drop tool call/result pairs from middle
        4. Emergency: system + last 2 exchanges
        """
        max_msgs = self._config.max_context_messages

        if len(messages) <= max_msgs:
            return messages

        # Stage 1: Truncate old tool/long results
        compressed = []
        for i, msg in enumerate(messages):
            if i < len(messages) - 20:  # Only compress old messages
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > 1000:
                    msg = {**msg, "content": content[:500] + "\n[...truncated...]"}
            compressed.append(msg)

        if len(compressed) <= max_msgs:
            return compressed

        # Stage 2: Sliding window
        # Keep first message (user prompt) + last N
        keep_recent = max_msgs - 1
        result = [compressed[0]] + compressed[-keep_recent:]

        if len(result) <= max_msgs:
            return result

        # Stage 3: Drop middle tool pairs
        final = [result[0]]
        i = 1
        while i < len(result):
            # Keep last 10 messages always
            if i >= len(result) - 10:
                final.append(result[i])
            elif result[i].get("role") != "tool":
                final.append(result[i])
            i += 1

        if len(final) <= max_msgs:
            return final

        # Stage 4: Emergency — system + last 2 exchanges
        return [messages[0]] + messages[-4:]

    def _is_ping_pong(self) -> bool:
        """Detect repeating patterns (A-B-A-B or A-B-C-A-B-C)."""
        seq = list(self._tool_sequence)
        if len(seq) < 4:
            return False

        # Check pattern length 2 (A-B-A-B)
        for pattern_len in (2, 3):
            if len(seq) >= pattern_len * 2:
                pattern = seq[-pattern_len:]
                before = seq[-(pattern_len * 2):-pattern_len]
                if pattern == before:
                    return True
        return False

    def reset(self) -> None:
        self._call_counts.clear()
        self._tool_sequence.clear()
        self._per_tool_counts.clear()
        self._agent_output_hashes.clear()
        self._warned_hashes.clear()
