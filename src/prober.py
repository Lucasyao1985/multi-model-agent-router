"""
Capability Prober
=================
Before trusting a model with real tasks, run it through a silent "entrance exam".
Probes 5 dimensions and returns a CapabilityProfile (radar chart data).

Dimensions:
  - logic        : Detects logical traps in code
  - instruction  : Follows strict output format (pure JSON, no prose)
  - tool_use     : Handles error API responses without hallucinating
  - code_gen     : Generates syntactically valid code
  - reasoning    : Multi-step deduction accuracy
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from .client import OpenRouterClient
from .router import TaskComplexity
from .utils.logger import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────
# Probe definitions
# ──────────────────────────────────────────────

PROBES = [
    {
        "id": "logic",
        "name": "Logic Trap Detection",
        "system": "You are a code reviewer. Respond ONLY with JSON: {\"has_bug\": true/false, \"reason\": \"...\"}",
        "prompt": (
            "Does this function have a bug?\n\n"
            "```python\n"
            "def divide(a, b):\n"
            "    if b == 0:\n"
            "        return 0\n"
            "    return a / b\n"
            "```\n"
            "Note: returning 0 for division by zero silently hides errors in most use cases."
        ),
        "check": lambda r: '"has_bug": true' in r.lower() or '"has_bug":true' in r.lower(),
        "weight": 1.5,
    },
    {
        "id": "instruction",
        "name": "Instruction Following",
        "system": "Respond ONLY with valid JSON. No markdown, no explanation, no extra text.",
        "prompt": 'Return a JSON object with keys "name" (string) and "score" (integer 42).',
        "check": lambda r: _is_pure_json(r),
        "weight": 2.0,
    },
    {
        "id": "tool_use",
        "name": "Error Resilience",
        "system": (
            "You are an API integration agent. When an API call returns an error, "
            "you must respond ONLY with JSON: {\"action\": \"retry\"|\"abort\"|\"fallback\", \"reason\": \"...\"}"
        ),
        "prompt": (
            "The weather API returned: {\"error\": 429, \"message\": \"rate limit exceeded\"}.\n"
            "What do you do?"
        ),
        "check": lambda r: any(
            w in r.lower() for w in ['"action": "retry"', '"action":"retry"',
                                      '"action": "fallback"', '"action":"fallback"']
        ),
        "weight": 1.5,
    },
    {
        "id": "code_gen",
        "name": "Code Generation",
        "system": "Output ONLY valid Python code. No explanation. No markdown fences.",
        "prompt": "Write a function `flatten(lst)` that flattens a nested list of any depth.",
        "check": lambda r: "def flatten" in r and "return" in r,
        "weight": 1.0,
    },
    {
        "id": "reasoning",
        "name": "Multi-step Reasoning",
        "system": "Respond ONLY with JSON: {\"answer\": <integer>, \"steps\": [\"step1\", ...]}",
        "prompt": (
            "A store sells apples for $1.20 each. "
            "Alice buys 3 apples and pays with a $5 bill. "
            "The cashier gives back change in the fewest possible coins "
            "(quarters=25¢, dimes=10¢, nickels=5¢, pennies=1¢). "
            "How many coins does Alice receive? Answer in JSON."
        ),
        # Correct: change = $5 - $3.60 = $1.40 → 5 coins (1 dollar? no coins > quarter)
        # $1.40 = 5×25¢ + 1×10¢ + 1×5¢ = 7 coins. Accept 7.
        "check": lambda r: '"answer": 7' in r or '"answer":7' in r,
        "weight": 2.0,
    },
]


def _is_pure_json(text: str) -> bool:
    clean = re.sub(r"```(?:json)?|```", "", text).strip()
    try:
        json.loads(clean)
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────
# Result types
# ──────────────────────────────────────────────

@dataclass
class ProbeResult:
    probe_id: str
    probe_name: str
    passed: bool
    latency_ms: float
    raw_response: str
    cost_usd: float


@dataclass
class CapabilityProfile:
    model_name: str
    scores: dict[str, float]          # {dimension: 0.0–1.0}
    overall_score: float              # weighted average
    recommended_role: str             # "manager" | "worker" | "reviewer" | "unsuitable"
    probe_results: list[ProbeResult] = field(default_factory=list)
    total_cost_usd: float = 0.0
    probe_time_sec: float = 0.0

    def radar(self) -> str:
        """ASCII radar summary for logging."""
        bars = {k: "█" * int(v * 10) + "░" * (10 - int(v * 10)) for k, v in self.scores.items()}
        lines = [f"  {k:<15} {bar}  {v:.0%}" for (k, bar), v in zip(bars.items(), self.scores.values())]
        return "\n".join([
            f"\n╔══ Capability Profile: {self.model_name} ══",
            *lines,
            f"  {'OVERALL':<15} {'█' * int(self.overall_score * 10)}  {self.overall_score:.0%}",
            f"  Recommended role: {self.recommended_role.upper()}",
            f"╚══ Cost: ${self.total_cost_usd:.5f} ══\n",
        ])


# ──────────────────────────────────────────────
# Prober
# ──────────────────────────────────────────────

class CapabilityProber:
    """
    Silently runs a battery of atomic probes on a model
    and returns a CapabilityProfile.
    """

    def __init__(self, client: OpenRouterClient):
        self.client = client

    def probe(
        self,
        model_key: str,             # key from MODEL_REGISTRY, e.g. "deepseek-chat"
        verbose: bool = True,
    ) -> CapabilityProfile:
        from .router import MODEL_REGISTRY
        if model_key not in MODEL_REGISTRY:
            raise ValueError(f"Unknown model key: {model_key}. Available: {list(MODEL_REGISTRY)}")

        model_cfg = MODEL_REGISTRY[model_key]
        logger.info(f"Probing {model_cfg.display_name} ({len(PROBES)} tests)...")

        t_start = time.monotonic()
        results: list[ProbeResult] = []
        scores: dict[str, float] = {}
        total_cost = 0.0

        for probe in PROBES:
            try:
                resp = self.client.chat(
                    prompt=probe["prompt"],
                    system=probe["system"],
                    force_complexity=TaskComplexity.LOW,  # use cheapest routing within the probe
                    max_tokens=512,
                    temperature=0.0,
                )
                passed = probe["check"](resp.content)
                pr = ProbeResult(
                    probe_id=probe["id"],
                    probe_name=probe["name"],
                    passed=passed,
                    latency_ms=resp.latency_ms,
                    raw_response=resp.content,
                    cost_usd=resp.estimated_cost_usd,
                )
                total_cost += resp.estimated_cost_usd
                icon = "✓" if passed else "✗"
                if verbose:
                    logger.info(f"  {icon} [{probe['id']}] {probe['name']} ({resp.latency_ms:.0f}ms)")
            except Exception as e:
                logger.warning(f"  ! [{probe['id']}] probe failed: {e}")
                pr = ProbeResult(
                    probe_id=probe["id"], probe_name=probe["name"],
                    passed=False, latency_ms=0, raw_response=str(e), cost_usd=0,
                )
            results.append(pr)
            scores[probe["id"]] = 1.0 if pr.passed else 0.0

        # Weighted overall score
        total_weight = sum(p["weight"] for p in PROBES)
        weighted_sum = sum(
            scores[p["id"]] * p["weight"] for p in PROBES
        )
        overall = weighted_sum / total_weight

        role = _assign_role(scores, overall)

        profile = CapabilityProfile(
            model_name=model_cfg.display_name,
            scores=scores,
            overall_score=overall,
            recommended_role=role,
            probe_results=results,
            total_cost_usd=total_cost,
            probe_time_sec=time.monotonic() - t_start,
        )
        if verbose:
            logger.info(profile.radar())
        return profile


def _assign_role(scores: dict[str, float], overall: float) -> str:
    """Map capability scores to a Team Code role."""
    if overall < 0.3:
        return "unsuitable"
    if scores.get("instruction", 0) >= 1.0 and scores.get("reasoning", 0) >= 1.0:
        return "manager"      # Can plan and follow strict output format
    if scores.get("code_gen", 0) >= 1.0 and overall >= 0.5:
        return "worker"       # Good at producing output
    if scores.get("logic", 0) >= 1.0:
        return "reviewer"     # Good at catching errors
    return "worker"           # Default fallback
