"""
Example 1: Basic routing — watch how different prompts route to different models
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import OpenRouterClient, TaskComplexity

client = OpenRouterClient()

# LOW complexity → Gemini Flash (cheapest)
resp = client.chat("What does the `zip()` function do in Python?")
print(f"[{resp.complexity.upper()}] {resp.model_used}: {resp.content[:120]}...")
print(f"  Cost: ${resp.estimated_cost_usd:.5f} | {resp.latency_ms:.0f}ms\n")

# MEDIUM complexity → DeepSeek V3
resp = client.chat("Write a Python function that parses a CSV file and returns a list of dicts.")
print(f"[{resp.complexity.upper()}] {resp.model_used}: {resp.content[:120]}...")
print(f"  Cost: ${resp.estimated_cost_usd:.5f} | {resp.latency_ms:.0f}ms\n")

# HIGH complexity → Claude Sonnet (extended thinking)
resp = client.chat(
    "Design a scalable architecture for a real-time collaborative code editor. "
    "Consider conflict resolution, latency, and offline support trade-offs.",
    extended_thinking=True,
)
print(f"[{resp.complexity.upper()}] {resp.model_used}: {resp.content[:200]}...")
print(f"  Cost: ${resp.estimated_cost_usd:.5f} | {resp.latency_ms:.0f}ms\n")

print("=== Session Stats ===")
print(client.get_stats())
