"""
OpenRouter API client with automatic model fallback.
"""

import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from dotenv import load_dotenv

from .router import ModelConfig, get_model_chain, TaskComplexity, score_complexity
from .utils.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT = 120  # seconds


@dataclass
class AgentResponse:
    content: str
    model_used: str
    complexity: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    fallback_triggered: bool = False
    estimated_cost_usd: float = 0.0


@dataclass
class UsageStats:
    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    fallback_count: int = 0
    complexity_breakdown: dict = field(default_factory=lambda: {"low": 0, "medium": 0, "high": 0})


class OpenRouterClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        site_url: str = "https://github.com/your-handle/multi-model-agent-router",
        app_name: str = "MultiModelAgentRouter",
    ):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is not set.")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": site_url,
            "X-Title": app_name,
            "Content-Type": "application/json",
        }
        self.stats = UsageStats()

    def _estimate_cost(self, model: ModelConfig, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1000 * model.cost_per_1k_input
            + output_tokens / 1000 * model.cost_per_1k_output
        )

    def _call_model(
        self,
        model: ModelConfig,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        extended_thinking: bool,
    ) -> dict:
        payload: dict = {
            "model": model.model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if extended_thinking and model.supports_extended_thinking:
            payload["thinking"] = {"type": "enabled", "budget_tokens": 8000}

        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            resp = client.post(OPENROUTER_API_URL, headers=self.headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    def chat(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.3,
        force_complexity: Optional[TaskComplexity] = None,
        extended_thinking: bool = False,
        history: Optional[list[dict]] = None,
    ) -> AgentResponse:
        """
        Send a prompt through the routing layer with automatic fallback.
        """
        complexity, model_chain = get_model_chain(prompt, force_complexity)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        last_error = None
        fallback_triggered = False

        for idx, model in enumerate(model_chain):
            if idx > 0:
                fallback_triggered = True
                logger.warning(f"Falling back to {model.display_name} after error: {last_error}")
                time.sleep(1)

            try:
                t0 = time.monotonic()
                data = self._call_model(model, messages, max_tokens, temperature, extended_thinking)
                latency_ms = (time.monotonic() - t0) * 1000

                usage = data.get("usage", {})
                input_tokens = usage.get("prompt_tokens", 0)
                output_tokens = usage.get("completion_tokens", 0)
                content = data["choices"][0]["message"]["content"]
                cost = self._estimate_cost(model, input_tokens, output_tokens)

                # Update global stats
                self.stats.total_calls += 1
                self.stats.total_input_tokens += input_tokens
                self.stats.total_output_tokens += output_tokens
                self.stats.total_cost_usd += cost
                if fallback_triggered:
                    self.stats.fallback_count += 1
                self.stats.complexity_breakdown[complexity.value] += 1

                logger.info(
                    f"✓ {model.display_name} | "
                    f"{input_tokens}+{output_tokens} tokens | "
                    f"${cost:.5f} | {latency_ms:.0f}ms"
                )

                return AgentResponse(
                    content=content,
                    model_used=model.display_name,
                    complexity=complexity.value,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    fallback_triggered=fallback_triggered,
                    estimated_cost_usd=cost,
                )

            except Exception as e:
                last_error = str(e)
                logger.error(f"✗ {model.display_name} failed: {last_error}")
                continue

        raise RuntimeError(
            f"All models in fallback chain failed. Last error: {last_error}"
        )

    def get_stats(self) -> dict:
        return {
            "total_calls": self.stats.total_calls,
            "total_tokens": self.stats.total_input_tokens + self.stats.total_output_tokens,
            "total_cost_usd": round(self.stats.total_cost_usd, 5),
            "fallback_rate": (
                round(self.stats.fallback_count / self.stats.total_calls, 3)
                if self.stats.total_calls else 0
            ),
            "complexity_breakdown": self.stats.complexity_breakdown,
        }
