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


# Model registry — update pricing as needed
MODEL_REGISTRY = {
    "claude-sonnet": ModelConfig(
        model_id="anthropic/claude-sonnet-4-5",
        display_name="Claude Sonnet 4.5",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        max_tokens=8192,
        supports_extended_thinking=True,
    ),
    "claude-haiku": ModelConfig(
        model_id="anthropic/claude-haiku-3-5",
        display_name="Claude Haiku 3.5",
        cost_per_1k_input=0.0008,
        cost_per_1k_output=0.004,
        max_tokens=4096,
    ),
    "deepseek-chat": ModelConfig(
        model_id="deepseek/deepseek-chat",
        display_name="DeepSeek V3",
        cost_per_1k_input=0.00027,
        cost_per_1k_output=0.0011,
        max_tokens=8192,
    ),
    "deepseek-r1": ModelConfig(
        model_id="deepseek/deepseek-r1",
        display_name="DeepSeek R1",
        cost_per_1k_input=0.00055,
        cost_per_1k_output=0.00219,
        max_tokens=8192,
    ),
    "gemini-flash": ModelConfig(
        model_id="google/gemini-2.0-flash-001",
        display_name="Gemini 2.0 Flash",
        cost_per_1k_input=0.0001,
        cost_per_1k_output=0.0004,
        max_tokens=8192,
    ),
}

# Routing table: complexity → [preferred, fallback1, fallback2]
ROUTING_TABLE = {
    TaskComplexity.LOW: ["gemini-flash", "deepseek-chat", "claude-haiku"],
    TaskComplexity.MEDIUM: ["deepseek-chat", "deepseek-r1", "claude-haiku"],
    TaskComplexity.HIGH: ["claude-sonnet", "deepseek-r1", "deepseek-chat"],
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
