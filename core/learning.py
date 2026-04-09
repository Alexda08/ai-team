"""Learning system: mine training data from traces and evolve agent configs.

Provides:
- TrainingDataMiner: extract SFT pairs and routing data from successful traces
- AgentConfigEvolver: analyze agent performance and write versioned configs
- LearningOrchestrator: run the full trace -> learn -> evolve cycle
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
import json
import os
import time
import re
import shutil


@dataclass
class TrainingPair:
    input_text: str
    output_text: str
    agent: str
    quality_score: float
    source_trace_id: str
    pair_type: str  # "sft", "routing", "config"


def classify_query(query: str) -> str:
    """Classify a query into a category for stratification."""
    q = query.lower()
    patterns = {
        "web_app": r"(?:web|app|frontend|backend|api|react|svelte|next|django|flask)",
        "cli_tool": r"(?:cli|command.?line|terminal|script|bash)",
        "data": r"(?:data|csv|json|database|sql|sqlite|postgres|mongo)",
        "mobile": r"(?:mobile|android|ios|flutter|react.?native)",
        "devops": r"(?:docker|deploy|ci|cd|kubernetes|terraform)",
        "testing": r"(?:test|spec|assert|mock|coverage)",
        "refactor": r"(?:refactor|clean|optimize|improve|performance)",
        "bugfix": r"(?:bug|fix|error|crash|issue|broken)",
    }
    for category, pattern in patterns.items():
        if re.search(pattern, q):
            return category
    return "general"


class TrainingDataMiner:
    """Extract training pairs from trace history."""

    def __init__(self, trace_store, min_quality: float = 0.7):
        self._store = trace_store
        self._min_quality = min_quality

    def extract_sft_pairs(self, agent: Optional[str] = None, limit: int = 500) -> List[TrainingPair]:
        """Extract supervised fine-tuning pairs from successful traces."""
        traces = self._store.list_traces(
            status="completed",
            outcome="success",
            limit=limit,
        )

        pairs = []
        seen = set()

        for trace in traces:
            if trace.feedback is not None and trace.feedback < self._min_quality:
                continue

            # Extract input/output from generate steps
            for step in trace.steps:
                if step.step_type.value == "generate" and step.output_summary:
                    if agent and step.agent != agent:
                        continue

                    key = (step.input_summary, step.output_summary)
                    if key in seen:
                        continue
                    seen.add(key)

                    pairs.append(TrainingPair(
                        input_text=step.input_summary,
                        output_text=step.output_summary,
                        agent=step.agent,
                        quality_score=trace.feedback or 0.8,
                        source_trace_id=trace.trace_id,
                        pair_type="sft",
                    ))

        return pairs

    def extract_routing_pairs(self, limit: int = 500) -> Dict[str, Dict]:
        """Extract model routing data: which model tier works best for which query class."""
        traces = self._store.list_traces(status="completed", limit=limit)

        class_data: Dict[str, Dict[str, list]] = {}

        for trace in traces:
            qclass = classify_query(trace.query)
            if qclass not in class_data:
                class_data[qclass] = {}

            # Look for model tier info in metadata
            model_tier = trace.metadata.get("model_tier", "unknown")
            if model_tier not in class_data[qclass]:
                class_data[qclass][model_tier] = []

            class_data[qclass][model_tier].append(trace.feedback or 0.5)

        # Compute best model per class
        routing = {}
        for qclass, tiers in class_data.items():
            best_tier = None
            best_avg = 0
            for tier, scores in tiers.items():
                avg = sum(scores) / len(scores) if scores else 0
                if avg > best_avg:
                    best_avg = avg
                    best_tier = tier
            routing[qclass] = {
                "best_model_tier": best_tier,
                "avg_feedback": best_avg,
                "sample_count": sum(len(s) for s in tiers.values()),
                "all_tiers": {t: {"avg": sum(s)/len(s), "count": len(s)} for t, s in tiers.items()},
            }

        return routing

    def extract_agent_config_pairs(self, limit: int = 500) -> Dict[str, Dict]:
        """Extract which tools and agents work best for each query class."""
        traces = self._store.list_traces(status="completed", limit=limit)

        class_data: Dict[str, Dict[str, Any]] = {}

        for trace in traces:
            qclass = classify_query(trace.query)
            if qclass not in class_data:
                class_data[qclass] = {"agents": {}, "tools": {}}

            for step in trace.steps:
                if step.step_type.value == "tool_call":
                    tool = step.metadata.get("tool", "unknown")
                    if tool not in class_data[qclass]["tools"]:
                        class_data[qclass]["tools"][tool] = {"count": 0, "feedback_sum": 0}
                    class_data[qclass]["tools"][tool]["count"] += 1
                    class_data[qclass]["tools"][tool]["feedback_sum"] += (trace.feedback or 0.5)

                if step.agent:
                    if step.agent not in class_data[qclass]["agents"]:
                        class_data[qclass]["agents"][step.agent] = {"count": 0, "feedback_sum": 0}
                    class_data[qclass]["agents"][step.agent]["count"] += 1

        return class_data


class AgentConfigEvolver:
    """Analyze agent performance from traces and write versioned config updates."""

    def __init__(self, trace_store, config_dir: str = "agent_configs"):
        self._store = trace_store
        self._config_dir = config_dir
        self._history_dir = os.path.join(config_dir, ".history")
        os.makedirs(self._config_dir, exist_ok=True)
        os.makedirs(self._history_dir, exist_ok=True)

    def analyze(self, window: int = 100) -> List[Dict[str, Any]]:
        """Analyze recent traces and produce recommendations."""
        traces = self._store.list_traces(status="completed", limit=window)

        # Group by query class
        by_class: Dict[str, list] = {}
        for trace in traces:
            qclass = classify_query(trace.query)
            by_class.setdefault(qclass, []).append(trace)

        recommendations = []
        for qclass, class_traces in by_class.items():
            rec = self._analyze_class(qclass, class_traces)
            if rec:
                recommendations.append(rec)

        return recommendations

    def _analyze_class(self, qclass: str, traces: list) -> Optional[Dict]:
        if len(traces) < 3:
            return None

        # Collect tool usage stats
        tool_scores: Dict[str, Dict[str, float]] = {}
        tool_call_counts: List[int] = []

        for trace in traces:
            call_count = 0
            for step in trace.steps:
                if step.step_type.value == "tool_call":
                    tool = step.metadata.get("tool", "unknown")
                    if tool not in tool_scores:
                        tool_scores[tool] = {"count": 0, "success": 0}
                    tool_scores[tool]["count"] += 1
                    if "success=True" in step.output_summary:
                        tool_scores[tool]["success"] += 1
                    call_count += 1
            tool_call_counts.append(call_count)

        # Rank tools by success rate
        ranked_tools = sorted(
            tool_scores.items(),
            key=lambda x: x[1]["success"] / max(x[1]["count"], 1),
            reverse=True,
        )

        # Compute recommended max_turns (75th percentile + 2)
        if tool_call_counts:
            sorted_counts = sorted(tool_call_counts)
            p75_idx = int(len(sorted_counts) * 0.75)
            recommended_max_turns = max(5, sorted_counts[p75_idx] + 2)
        else:
            recommended_max_turns = 10

        return {
            "query_class": qclass,
            "sample_count": len(traces),
            "recommended_tools": [name for name, _ in ranked_tools[:10]],
            "recommended_max_turns": recommended_max_turns,
            "tool_stats": {name: stats for name, stats in ranked_tools},
        }

    def write_config(self, agent_name: str, updates: Dict[str, Any]) -> str:
        """Write a versioned agent config file.

        Archives existing config to .history/ before overwriting.
        """
        config_path = os.path.join(self._config_dir, f"{agent_name}.json")

        # Archive existing
        if os.path.isfile(config_path):
            self._archive(agent_name, config_path)

        # Merge with existing or create new
        existing = {}
        if os.path.isfile(config_path):
            with open(config_path, "r") as f:
                existing = json.load(f)

        merged = {**existing, **updates, "updated_at": time.time()}

        with open(config_path, "w") as f:
            json.dump(merged, f, indent=2)

        return config_path

    def _archive(self, agent_name: str, config_path: str) -> None:
        versions = self.list_versions(agent_name)
        next_version = len(versions) + 1
        archive_path = os.path.join(self._history_dir, f"{agent_name}.v{next_version}.json")
        shutil.copy2(config_path, archive_path)

    def list_versions(self, agent_name: str) -> List[Dict]:
        """List all archived versions of an agent config."""
        versions = []
        for fname in sorted(os.listdir(self._history_dir)):
            if fname.startswith(f"{agent_name}.v") and fname.endswith(".json"):
                version_num = int(fname.split(".v")[1].split(".json")[0])
                fpath = os.path.join(self._history_dir, fname)
                versions.append({
                    "version": version_num,
                    "path": fpath,
                    "modified": os.path.getmtime(fpath),
                })
        return versions

    def rollback(self, agent_name: str, version: int) -> None:
        """Restore a specific version as the current config."""
        archive_path = os.path.join(self._history_dir, f"{agent_name}.v{version}.json")
        if not os.path.isfile(archive_path):
            raise FileNotFoundError(f"Version {version} not found for agent '{agent_name}'")

        config_path = os.path.join(self._config_dir, f"{agent_name}.json")
        # Archive current before rollback
        if os.path.isfile(config_path):
            self._archive(agent_name, config_path)
        shutil.copy2(archive_path, config_path)


class LearningOrchestrator:
    """Orchestrates the full trace -> learn -> evolve cycle."""

    def __init__(
        self,
        trace_store,
        config_dir: str = "agent_configs",
        min_quality: float = 0.7,
        eval_fn: Optional[Callable] = None,
    ):
        self._store = trace_store
        self._miner = TrainingDataMiner(trace_store, min_quality=min_quality)
        self._evolver = AgentConfigEvolver(trace_store, config_dir=config_dir)
        self._eval_fn = eval_fn

    def run_learning_cycle(self) -> Dict[str, Any]:
        """Execute one learning cycle.

        Steps:
        1. Mine SFT pairs from successful traces
        2. Extract routing data
        3. Analyze agent performance and evolve configs
        4. Report summary
        """
        report = {
            "timestamp": time.time(),
            "status": "completed",
            "sft_pairs": 0,
            "routing_classes": 0,
            "recommendations": [],
            "configs_updated": [],
        }

        # Step 1: Mine SFT pairs
        sft_pairs = self._miner.extract_sft_pairs()
        report["sft_pairs"] = len(sft_pairs)

        if not sft_pairs:
            report["status"] = "skipped"
            report["reason"] = "No qualifying traces found"
            return report

        # Step 2: Extract routing data
        routing = self._miner.extract_routing_pairs()
        report["routing_classes"] = len(routing)

        # Step 3: Analyze and evolve
        recommendations = self._evolver.analyze()
        report["recommendations"] = recommendations

        # Apply recommendations to agent configs
        for rec in recommendations:
            qclass = rec["query_class"]
            config_path = self._evolver.write_config(
                agent_name=f"optimized_{qclass}",
                updates={
                    "query_class": qclass,
                    "recommended_tools": rec["recommended_tools"],
                    "recommended_max_turns": rec["recommended_max_turns"],
                    "sample_count": rec["sample_count"],
                },
            )
            report["configs_updated"].append(config_path)

        # Step 4: Optional evaluation
        if self._eval_fn:
            try:
                eval_score = self._eval_fn()
                report["eval_score"] = eval_score
            except Exception as e:
                report["eval_error"] = str(e)

        # Save SFT pairs for potential training
        pairs_path = os.path.join(self._evolver._config_dir, "sft_pairs.json")
        with open(pairs_path, "w") as f:
            json.dump([{
                "input": p.input_text,
                "output": p.output_text,
                "agent": p.agent,
                "quality": p.quality_score,
                "trace_id": p.source_trace_id,
            } for p in sft_pairs], f, indent=2)

        report["sft_pairs_path"] = pairs_path
        return report
