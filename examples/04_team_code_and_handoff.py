"""
Example 4: Team Code (光明神合体) + Model Handoff (SHP)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import OpenRouterClient
from src.agents.team_agent import TeamCodeOrchestrator
from src.handoff import HandoffManager
from src.skill_registry import SkillRegistry

client = OpenRouterClient()

# ── Demo 1: Team Code ──────────────────────────────────────────────
print("=" * 60)
print("DEMO 1: Team Code Orchestration")
print("Manager(Claude) → Worker(DeepSeek) → Reviewer(Gemini)")
print("=" * 60)

orchestrator = TeamCodeOrchestrator(client)

result = orchestrator.run(
    task=(
        "Build a Python rate limiter class that:\n"
        "1. Uses a sliding window algorithm\n"
        "2. Supports per-key limits (e.g., per user ID)\n"
        "3. Is thread-safe\n"
        "4. Has a method `is_allowed(key, limit, window_seconds) -> bool`"
    )
)

print("\n--- Final Output ---")
print(result.final_output[:1500])
print(f"\n{result.summary()}")


# ── Demo 2: SHP Model Handoff ──────────────────────────────────────
print("\n" + "=" * 60)
print("DEMO 2: Structured Handoff Package (Model Switch)")
print("=" * 60)

# Simulate a conversation that was happening with Model A
fake_history = [
    {"role": "user", "content": "I want to build an n8n workflow that monitors a Slack channel and creates Jira tickets automatically."},
    {"role": "assistant", "content": "Great idea. I'll start by setting up the Slack trigger node..."},
    {"role": "user", "content": "The Slack webhook is at https://hooks.slack.com/abc123. The Jira API key is in env.JIRA_KEY. I tried using the HTTP Request node but got a 401 error."},
    {"role": "assistant", "content": "The 401 means authentication is wrong. For Jira Cloud you need Basic Auth with email:token as base64. Let me fix the HTTP node config..."},
    {"role": "user", "content": "It's still failing. Can we switch to using the Jira community node instead of raw HTTP?"},
    {"role": "assistant", "content": "Yes, install the n8n-nodes-jira package. I'll restructure the workflow..."},
]

manager = HandoffManager(client)

print("Compressing conversation history into SHP...")
shp = manager.compress(fake_history, from_model="claude-sonnet")
print(f"\nSHP created:")
print(f"  Goal: {shp.current_goal}")
print(f"  Progress: {shp.progress_pct}%")
print(f"  Validated skills: {shp.validated_skills}")
print(f"  Failed approaches: {[f['approach'] for f in shp.failed_approaches]}")
print(f"  Memory anchors: {list(shp.memory_anchors.keys())}")

print("\nInjecting SHP into new model (DeepSeek)...")
response = manager.inject(
    shp=shp,
    new_prompt="Please continue from where we left off and complete the Jira node setup.",
    to_model_key="deepseek-chat",
)
print(f"\nNew model response:\n{response[:600]}...")


# ── Demo 3: Skill Registry ─────────────────────────────────────────
print("\n" + "=" * 60)
print("DEMO 3: Skill Validation Registry")
print("=" * 60)

registry = SkillRegistry()
print(f"Built-in skills: {[s.id for s in registry.all_skills()]}")

# Validate a skill on a specific model
result_v = registry.validate_skill("pure_json_output", client, "deepseek-chat", runs=3)
print(f"\nSkill 'pure_json_output' on DeepSeek: {result_v['pass_rate']:.0%} pass rate")
if result_v["failures"]:
    print(f"  Failures: {result_v['failures']}")

# Export skill registry as SKILL.md
markdown = registry.export_markdown()
with open("SKILLS.md", "w") as f:
    f.write(markdown)
print("\nSkill registry exported to SKILLS.md")
