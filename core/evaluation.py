"""Enhanced evaluation framework.

Provides:
- Scorer ABC with multiple implementations (ExactMatch, CodeQuality, LLMJudge)
- EvalRunner for running evaluation suites
- EvalResult and RunSummary for structured metrics
"""

from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple
import time
import json
import re
import os
from concurrent.futures import ThreadPoolExecutor


@dataclass
class EvalCase:
    """A single evaluation case."""
    case_id: str
    input_data: Dict[str, Any]
    expected: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalResult:
    """Result of evaluating one case."""
    case_id: str
    model_answer: str = ""
    is_correct: Optional[bool] = None
    score: float = 0.0
    latency_ms: float = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MetricStats:
    mean: float = 0
    median: float = 0
    min: float = 0
    max: float = 0
    std: float = 0
    p90: float = 0
    p95: float = 0

    @classmethod
    def from_values(cls, values: List[float]) -> "MetricStats":
        if not values:
            return cls()
        sorted_v = sorted(values)
        n = len(sorted_v)
        mean = sum(sorted_v) / n
        median = sorted_v[n // 2]
        variance = sum((x - mean) ** 2 for x in sorted_v) / n
        return cls(
            mean=round(mean, 4),
            median=round(median, 4),
            min=round(sorted_v[0], 4),
            max=round(sorted_v[-1], 4),
            std=round(variance ** 0.5, 4),
            p90=round(sorted_v[int(n * 0.9)], 4),
            p95=round(sorted_v[int(n * 0.95)], 4),
        )


@dataclass
class RunSummary:
    """Aggregate results of an evaluation run."""
    total: int = 0
    scored: int = 0
    correct: int = 0
    accuracy: float = 0
    errors: int = 0
    results: List[EvalResult] = field(default_factory=list)
    latency_stats: Optional[MetricStats] = None
    score_stats: Optional[MetricStats] = None
    per_category: Dict[str, Dict[str, float]] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Scorers
# ---------------------------------------------------------------------------

class Scorer(ABC):
    @abstractmethod
    def score(self, prediction: str, reference: Optional[str], context: Optional[Dict] = None) -> Tuple[Optional[bool], float, Dict]:
        """Score a prediction against a reference.

        Returns (is_correct, score, metadata).
        """
        ...


class ExactMatchScorer(Scorer):
    """Score by exact string match (case-insensitive, stripped)."""

    def score(self, prediction: str, reference: Optional[str], context: Optional[Dict] = None) -> Tuple[Optional[bool], float, Dict]:
        if reference is None:
            return None, 0.0, {"reason": "no reference"}
        match = prediction.strip().lower() == reference.strip().lower()
        return match, 1.0 if match else 0.0, {}


class ContainsScorer(Scorer):
    """Score by checking if reference appears in prediction."""

    def score(self, prediction: str, reference: Optional[str], context: Optional[Dict] = None) -> Tuple[Optional[bool], float, Dict]:
        if reference is None:
            return None, 0.0, {"reason": "no reference"}
        match = reference.strip().lower() in prediction.strip().lower()
        return match, 1.0 if match else 0.0, {}


class CodeQualityScorer(Scorer):
    """Score code output based on structural quality checks."""

    def score(self, prediction: str, reference: Optional[str], context: Optional[Dict] = None) -> Tuple[Optional[bool], float, Dict]:
        checks = {
            "has_content": len(prediction.strip()) > 0,
            "no_syntax_errors": self._check_syntax(prediction),
            "balanced_brackets": self._check_brackets(prediction),
            "has_imports": bool(re.search(r"(?:import|from|require|use)", prediction)),
        }

        passed = sum(1 for v in checks.values() if v)
        total = len(checks)
        score = passed / total

        return score >= 0.75, score, {"checks": checks}

    def _check_syntax(self, code: str) -> bool:
        try:
            compile(code, "<eval>", "exec")
            return True
        except SyntaxError:
            return False
        except Exception:
            return True  # Non-Python code, assume OK

    def _check_brackets(self, code: str) -> bool:
        stack = []
        pairs = {")": "(", "]": "[", "}": "{"}
        for ch in code:
            if ch in "([{":
                stack.append(ch)
            elif ch in ")]}":
                if not stack or stack[-1] != pairs[ch]:
                    return False
                stack.pop()
        return len(stack) == 0


class LLMJudgeScorer(Scorer):
    """Use an LLM to judge prediction quality."""

    def __init__(self, llm_client, criteria: str = "accuracy and completeness"):
        self._llm = llm_client
        self._criteria = criteria

    def score(self, prediction: str, reference: Optional[str], context: Optional[Dict] = None) -> Tuple[Optional[bool], float, Dict]:
        prompt = f"""Evaluate the following prediction on {self._criteria}.

Reference (expected): {reference or 'N/A'}

Prediction: {prediction[:2000]}

Rate on a scale of 0.0 to 1.0. Respond with ONLY a JSON object:
{{"score": <float>, "reasoning": "<brief explanation>"}}"""

        try:
            response = self._llm.generate(
                system="You are an evaluation judge. Respond only with JSON.",
                messages=[{"role": "user", "content": prompt}],
            )
            # Parse JSON from response
            json_match = re.search(r"\{[^}]+\}", response)
            if json_match:
                data = json.loads(json_match.group())
                score = float(data.get("score", 0))
                return score >= 0.7, score, {"reasoning": data.get("reasoning", "")}
        except Exception as e:
            return None, 0.0, {"error": str(e)}

        return None, 0.0, {"error": "Could not parse judge response"}


# ---------------------------------------------------------------------------
# EvalRunner
# ---------------------------------------------------------------------------

class EvalRunner:
    """Run evaluation suites with multiple scorers."""

    def __init__(
        self,
        scorers: Optional[List[Scorer]] = None,
        event_bus=None,
    ):
        self._scorers = scorers or [ExactMatchScorer()]
        self._event_bus = event_bus

    def run(
        self,
        cases: List[EvalCase],
        agent_fn: Callable[[Dict], str],
        parallel: bool = False,
        max_workers: int = 4,
    ) -> RunSummary:
        """Run evaluation on a list of cases.

        Args:
            cases: evaluation cases
            agent_fn: callable that takes input_data dict and returns prediction string
            parallel: whether to run cases in parallel
            max_workers: max parallel workers
        """
        if self._event_bus:
            from core.events import EventType
            self._event_bus.publish(EventType.EVAL_START, {"total_cases": len(cases)})

        results = []

        if parallel:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(self._process_one, case, agent_fn): case for case in cases}
                for future in futures:
                    results.append(future.result())
        else:
            for case in cases:
                results.append(self._process_one(case, agent_fn))

        summary = self._aggregate(results)

        if self._event_bus:
            from core.events import EventType
            self._event_bus.publish(EventType.EVAL_END, {
                "accuracy": summary.accuracy,
                "total": summary.total,
            })

        return summary

    def run_from_file(self, path: str, agent_fn: Callable[[Dict], str], **kwargs) -> RunSummary:
        """Load cases from a JSON file and run evaluation."""
        with open(path, "r") as f:
            data = json.load(f)

        cases = [
            EvalCase(
                case_id=item.get("id", str(i)),
                input_data=item.get("input", item),
                expected=item.get("expected"),
                metadata=item.get("metadata", {}),
            )
            for i, item in enumerate(data)
        ]
        return self.run(cases, agent_fn, **kwargs)

    def _process_one(self, case: EvalCase, agent_fn: Callable) -> EvalResult:
        start = time.time()
        try:
            prediction = agent_fn(case.input_data)
        except Exception as e:
            return EvalResult(
                case_id=case.case_id,
                model_answer="",
                is_correct=False,
                score=0.0,
                latency_ms=(time.time() - start) * 1000,
                metadata={"error": str(e)},
            )

        latency_ms = (time.time() - start) * 1000

        # Run all scorers and average
        scores = []
        all_metadata = {}
        is_correct_votes = []

        for scorer in self._scorers:
            correct, score, meta = scorer.score(prediction, case.expected, case.metadata)
            scores.append(score)
            if correct is not None:
                is_correct_votes.append(correct)
            all_metadata[type(scorer).__name__] = meta

        avg_score = sum(scores) / len(scores) if scores else 0
        majority_correct = (sum(is_correct_votes) / len(is_correct_votes) > 0.5) if is_correct_votes else None

        return EvalResult(
            case_id=case.case_id,
            model_answer=prediction,
            is_correct=majority_correct,
            score=avg_score,
            latency_ms=latency_ms,
            metadata=all_metadata,
        )

    def _aggregate(self, results: List[EvalResult]) -> RunSummary:
        total = len(results)
        scored = sum(1 for r in results if r.is_correct is not None)
        correct = sum(1 for r in results if r.is_correct is True)
        errors = sum(1 for r in results if "error" in r.metadata)

        latencies = [r.latency_ms for r in results]
        scores = [r.score for r in results]

        return RunSummary(
            total=total,
            scored=scored,
            correct=correct,
            accuracy=correct / scored if scored > 0 else 0,
            errors=errors,
            results=results,
            latency_stats=MetricStats.from_values(latencies),
            score_stats=MetricStats.from_values(scores),
        )
