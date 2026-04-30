"""
Multi-Model Router
Automatically routes tasks to the optimal model based on complexity scoring.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .utils.logger import get_logger

logger = get_logger(__name__)


class TaskComplexity(Enum):
    LOW = "low"          # Simple lookup, formatting, translation
    MEDIUM = "medium"    # Code generation, summarization
    HIGH = "high"        # Architecture design, complex reasoning, multi-step debug


@dataclass
class ModelConfig:
    model_id: str
    display_name: str
    cost_per_1k_input: float   # USD
    cost_per_1k_output: float
    max_tokens: int
    supports_extended_thinking: bool = False


# Model registry — free models on OpenRouter (all pricing = $0)
# openrouter/free is an auto-router that picks any available free model
MODEL_REGISTRY = {
    "openrouter-free": ModelConfig(
        model_id="openrouter/free",
        display_name="OpenRouter Free Auto",
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        max_tokens=8192,
    ),
    "hermes-405b": ModelConfig(
        model_id="nousresearch/hermes-3-llama-3.1-405b:free",
        display_name="Hermes 3 405B",
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        max_tokens=8192,
    ),
    "nemotron-120b": ModelConfig(
        model_id="nvidia/nemotron-3-super-120b-a12b:free",
        display_name="Nemotron 3 Super 120B",
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        max_tokens=8192,
    ),
    "qwen3-coder": ModelConfig(
        model_id="qwen/qwen3-coder:free",
        display_name="Qwen3 Coder",
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        max_tokens=8192,
    ),
    "llama-70b": ModelConfig(
        model_id="meta-llama/llama-3.3-70b-instruct:free",
        display_name="Llama 3.3 70B",
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        max_tokens=8192,
    ),
    "gemma4-31b": ModelConfig(
        model_id="google/gemma-4-31b-it:free",
        display_name="Gemma 4 31B",
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        max_tokens=8192,
    ),
    "gemma3-12b": ModelConfig(
        model_id="google/gemma-3-12b-it:free",
        display_name="Gemma 3 12B",
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        max_tokens=4096,
    ),
    "gemma3-4b": ModelConfig(
        model_id="google/gemma-3-4b-it:free",
        display_name="Gemma 3 4B",
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        max_tokens=4096,
    ),
}

# Routing table: complexity → [preferred, fallback1, fallback2, ...]
# Specific models preferred when available, openrouter/free as reliable fallback
# Note: specific free models may be rate-limited; openrouter/free auto-routes to any available free model
ROUTING_TABLE = {
    TaskComplexity.LOW: ["gemma3-4b", "openrouter-free", "gemma3-12b"],
    TaskComplexity.MEDIUM: ["qwen3-coder", "openrouter-free", "gemma4-31b"],
    TaskComplexity.HIGH: ["hermes-405b", "openrouter-free", "nemotron-120b"],
}

# Keywords that bump up complexity
HIGH_COMPLEXITY_SIGNALS = [
    r"\barchitect\b", r"\bdesign pattern\b", r"\brefactor\b",
    r"\bsecurity audit\b", r"\bperformance optim\b", r"\bmulti.?step\b",
    r"\bwhy does\b", r"\bdebug\b", r"\broot cause\b",
    r"\btrade.?off\b", r"\bscalab\b",
]

MEDIUM_COMPLEXITY_SIGNALS = [
    r"\bgenerate\b", r"\bwrite\b", r"\bimplement\b", r"\bconvert\b",
    r"\bsummariz\b", r"\bexplain\b", r"\bunit test\b",
]


def score_complexity(prompt: str) -> TaskComplexity:
    """
    Heuristic complexity scoring.
    Returns TaskComplexity enum value.
    """
    lower = prompt.lower()
    word_count = len(prompt.split())

    high_hits = sum(1 for p in HIGH_COMPLEXITY_SIGNALS if re.search(p, lower))
    medium_hits = sum(1 for p in MEDIUM_COMPLEXITY_SIGNALS if re.search(p, lower))

    if high_hits >= 1 or word_count > 300:
        return TaskComplexity.HIGH
    elif medium_hits >= 1 or word_count > 80:
        return TaskComplexity.MEDIUM
    else:
        return TaskComplexity.LOW


def get_model_chain(
    prompt: str,
    force_complexity: Optional[TaskComplexity] = None,
) -> tuple[TaskComplexity, list[ModelConfig]]:
    """
    Returns (complexity, ordered list of ModelConfigs) for fallback chain.
    """
    complexity = force_complexity or score_complexity(prompt)
    model_keys = ROUTING_TABLE[complexity]
    chain = [MODEL_REGISTRY[k] for k in model_keys if k in MODEL_REGISTRY]
    logger.info(f"Complexity={complexity.value} → chain={[m.display_name for m in chain]}")
    return complexity, chain
