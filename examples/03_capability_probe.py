"""
Example 3: Capability Probe — give a new model its "entrance exam" before real work.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import OpenRouterClient
from src.prober import CapabilityProber

client = OpenRouterClient()
prober = CapabilityProber(client)

# Probe multiple models and compare
models_to_test = ["deepseek-chat", "gemini-flash", "claude-haiku"]

profiles = {}
for model_key in models_to_test:
    print(f"\nProbing {model_key}...")
    profile = prober.probe(model_key)
    profiles[model_key] = profile

# Print comparison table
print("\n" + "="*60)
print("CAPABILITY COMPARISON")
print("="*60)
print(f"{'Model':<20} {'Overall':>8} {'Role':<12} {'Cost':>10}")
print("-"*60)
for key, p in profiles.items():
    print(f"{p.model_name:<20} {p.overall_score:>7.0%} {p.recommended_role:<12} ${p.total_cost_usd:.5f}")

# Auto-assign Team Code roles
print("\n--- Recommended Team Code Lineup ---")
managers  = [(k, p) for k, p in profiles.items() if p.recommended_role == "manager"]
workers   = [(k, p) for k, p in profiles.items() if p.recommended_role == "worker"]
reviewers = [(k, p) for k, p in profiles.items() if p.recommended_role == "reviewer"]

print(f"Manager : {managers[0][1].model_name  if managers  else 'none — use claude-sonnet'}")
print(f"Worker  : {workers[0][1].model_name   if workers   else 'none — use deepseek-chat'}")
print(f"Reviewer: {reviewers[0][1].model_name if reviewers else 'none — use gemini-flash'}")
