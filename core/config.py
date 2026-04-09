from dataclasses import dataclass, field
from typing import Optional, Dict
import os
import sys

# Python 3.11+ has tomllib in stdlib; older versions need tomli
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None


@dataclass
class LLMConfig:
    provider: str = "ollama"
    host: str = "http://localhost:11434"
    default_model: str = "minimax-m2.7:cloud"
    temperature: float = 0.7
    model_map: Dict[str, str] = field(default_factory=lambda: {
        "light": "minimax-m2.7:cloud",
        "medium": "qwen3.5:cloud",
        "heavy": "qwen3-coder-next:cloud",
        "ultra": "qwen3-coder:480b-cloud",
    })


@dataclass
class RuntimeConfig:
    max_turns: int = 10
    max_retries: int = 2
    workspace_path: str = "./workspace"
    output_path: str = "./output"


@dataclass
class TracesConfig:
    enabled: bool = False
    db_path: str = "traces/traces.db"


@dataclass
class SecurityConfig:
    enabled: bool = False
    policies: Dict[str, list] = field(default_factory=dict)


@dataclass
class LearningConfig:
    enabled: bool = False
    min_quality: float = 0.7
    min_improvement: float = 0.02
    config_dir: str = "agent_configs"


@dataclass
class EvaluationConfig:
    enabled: bool = False
    parallel: bool = False


@dataclass
class DevTeamConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    traces: TracesConfig = field(default_factory=TracesConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)


def _apply_section(target, data: dict) -> None:
    """Recursively apply TOML dict values onto a dataclass instance."""
    for key, value in data.items():
        if hasattr(target, key):
            current = getattr(target, key)
            if isinstance(current, dict) and isinstance(value, dict):
                current.update(value)
            else:
                setattr(target, key, value)


def load_config(path: Optional[str] = None) -> DevTeamConfig:
    """Load config from TOML file.

    Resolution order:
      1. Explicit *path* argument
      2. DEVTEAM_CONFIG environment variable
      3. ./config.toml (project root)
      4. Pure defaults (no file needed)
    """
    config = DevTeamConfig()

    config_path = (
        path
        or os.environ.get("DEVTEAM_CONFIG")
        or "config.toml"
    )

    if os.path.isfile(config_path):
        if tomllib is None:
            print("[WARN] config.toml found but tomllib/tomli not installed. Using defaults.")
            return config

        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        section_map = {
            "llm": config.llm,
            "runtime": config.runtime,
            "traces": config.traces,
            "security": config.security,
            "learning": config.learning,
            "evaluation": config.evaluation,
        }

        for section_name, target in section_map.items():
            if section_name in data:
                _apply_section(target, data[section_name])

    return config
