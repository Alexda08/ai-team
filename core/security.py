"""Security layer: capability-based access control and content scanning.

Provides:
- Capability enum for fine-grained permissions
- CapabilityPolicy per agent (grant/deny/check)
- Default policies per agent role
- BoundaryGuard for scanning outbound content
- Built-in scanners: PathTraversalScanner, SecretScanner
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set
import re


class Capability(Enum):
    FILE_READ = "FILE_READ"
    FILE_WRITE = "FILE_WRITE"
    NETWORK_FETCH = "NETWORK_FETCH"
    CODE_EXECUTE = "CODE_EXECUTE"
    SHELL_ACCESS = "SHELL_ACCESS"
    PROCESS_MANAGE = "PROCESS_MANAGE"


@dataclass
class CapabilityPolicy:
    """Capability-based access control for a single agent."""
    agent_name: str
    granted: Set[str] = field(default_factory=set)
    denied: Set[str] = field(default_factory=set)

    def check(self, capability: str) -> bool:
        if capability in self.denied:
            return False
        return capability in self.granted

    def grant(self, *capabilities: str) -> None:
        for cap in capabilities:
            self.granted.add(cap)
            self.denied.discard(cap)

    def deny(self, *capabilities: str) -> None:
        for cap in capabilities:
            self.denied.add(cap)
            self.granted.discard(cap)


class PolicyManager:
    """Manages capability policies for all agents."""

    def __init__(self, default_deny: bool = False):
        self._policies: Dict[str, CapabilityPolicy] = {}
        self._default_deny = default_deny

    def set_policy(self, policy: CapabilityPolicy) -> None:
        self._policies[policy.agent_name] = policy

    def get_policy(self, agent_name: str) -> Optional[CapabilityPolicy]:
        return self._policies.get(agent_name)

    def check(self, agent_name: str, capability: str) -> bool:
        policy = self._policies.get(agent_name)
        if policy is None:
            return not self._default_deny
        return policy.check(capability)

    def load_defaults(self) -> None:
        """Load default policies for standard agent roles."""
        for name, policy in DEFAULT_POLICIES.items():
            self._policies[name] = policy


# Default capability policies per agent role
DEFAULT_POLICIES: Dict[str, CapabilityPolicy] = {
    "Thinker": CapabilityPolicy("Thinker", granted=set()),
    "Critic": CapabilityPolicy("Critic", granted=set()),
    "Architect": CapabilityPolicy("Architect", granted=set()),
    "Tasker": CapabilityPolicy("Tasker", granted=set()),
    "Coder": CapabilityPolicy("Coder", granted={
        Capability.FILE_READ.value,
        Capability.FILE_WRITE.value,
    }),
    "Executor": CapabilityPolicy("Executor", granted={
        Capability.FILE_READ.value,
        Capability.FILE_WRITE.value,
        Capability.CODE_EXECUTE.value,
        Capability.SHELL_ACCESS.value,
        Capability.PROCESS_MANAGE.value,
    }),
    "Validator": CapabilityPolicy("Validator", granted={
        Capability.FILE_READ.value,
    }),
}


# ---------------------------------------------------------------------------
# Content scanning
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    clean: bool
    issues: List[str] = field(default_factory=list)


class BaseScanner(ABC):
    scanner_id: str = "base"

    @abstractmethod
    def scan(self, content: str, context: Optional[Dict] = None) -> ScanResult:
        ...

    @abstractmethod
    def redact(self, content: str) -> str:
        ...


class PathTraversalScanner(BaseScanner):
    """Detects path traversal attempts (../../etc/passwd)."""
    scanner_id = "path_traversal"

    _PATTERNS = [
        re.compile(r"\.\./\.\./"),                  # ../../../
        re.compile(r"\.\.[/\\]"),                     # ../ or ..\
        re.compile(r"(?:^|/)etc/(?:passwd|shadow)"),  # /etc/passwd
        re.compile(r"(?:^|[/\\])(?:C:|D:)[/\\]", re.IGNORECASE),  # C:\
    ]

    def scan(self, content: str, context: Optional[Dict] = None) -> ScanResult:
        issues = []
        for pattern in self._PATTERNS:
            if pattern.search(content):
                issues.append(f"Path traversal pattern detected: {pattern.pattern}")
        return ScanResult(clean=len(issues) == 0, issues=issues)

    def redact(self, content: str) -> str:
        for pattern in self._PATTERNS:
            content = pattern.sub("[REDACTED_PATH]", content)
        return content


class SecretScanner(BaseScanner):
    """Detects potential secrets, API keys, and credentials in content."""
    scanner_id = "secret"

    _PATTERNS = [
        (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key"),
        (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "API Secret Key"),
        (re.compile(r"ghp_[a-zA-Z0-9]{36}"), "GitHub Personal Token"),
        (re.compile(r"glpat-[a-zA-Z0-9\-]{20,}"), "GitLab Token"),
        (re.compile(r"(?i)password\s*[=:]\s*['\"][^'\"]{4,}['\"]"), "Hardcoded Password"),
        (re.compile(r"(?i)api[_-]?key\s*[=:]\s*['\"][^'\"]{8,}['\"]"), "Hardcoded API Key"),
        (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"), "Private Key"),
    ]

    def scan(self, content: str, context: Optional[Dict] = None) -> ScanResult:
        issues = []
        for pattern, label in self._PATTERNS:
            if pattern.search(content):
                issues.append(f"Potential secret detected: {label}")
        return ScanResult(clean=len(issues) == 0, issues=issues)

    def redact(self, content: str) -> str:
        for pattern, label in self._PATTERNS:
            content = pattern.sub(f"[REDACTED_{label.upper().replace(' ', '_')}]", content)
        return content


class BoundaryGuard:
    """Scans outbound content using registered scanners.

    Modes:
    - "redact": replace detected patterns with placeholders
    - "warn": report issues but allow
    - "block": reject content with issues
    """

    def __init__(
        self,
        mode: str = "warn",
        scanners: Optional[List[BaseScanner]] = None,
        event_bus=None,
    ):
        self._mode = mode
        self._scanners = scanners or [PathTraversalScanner(), SecretScanner()]
        self._event_bus = event_bus

    def scan_outbound(self, content: str, context: Optional[Dict] = None) -> List[str]:
        """Scan content and return list of issues found."""
        all_issues = []
        for scanner in self._scanners:
            result = scanner.scan(content, context)
            all_issues.extend(result.issues)

        if all_issues and self._event_bus:
            from core.events import EventType
            self._event_bus.publish(EventType.BOUNDARY_BLOCKED, {
                "issues": all_issues,
                "mode": self._mode,
                "context": context or {},
            })

        return all_issues

    def check_outbound(self, content: str, context: Optional[Dict] = None) -> str:
        """Check content and apply mode-specific handling.

        Returns the (potentially redacted) content, or raises ValueError in block mode.
        """
        issues = self.scan_outbound(content, context)

        if not issues:
            return content

        if self._mode == "block":
            raise ValueError(f"Content blocked by security scan: {'; '.join(issues)}")

        if self._mode == "redact":
            for scanner in self._scanners:
                content = scanner.redact(content)

        return content
