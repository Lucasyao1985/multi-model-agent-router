# 🔀 Multi-Model Agent Router

> Route tasks to the optimal model, switch models without losing context, and fuse multiple mid-tier models into a team that rivals a flagship.

Built on [OpenRouter](https://openrouter.ai). No proprietary infrastructure — just Python + API keys.

---

## The Problem

Working with multiple AI models in real development looks like this:

- A skill works perfectly on Claude. You switch to DeepSeek to save cost. It breaks.
- You can't afford Opus 4.6 for everything, but cheaper models miss things Opus wouldn't.
- Switching models mid-task means the new model has no memory of what was validated, what failed, or why.
- You have no way to know *before* deploying a model whether it can execute your skill.

This project solves all four.

---

## Architecture

```
                    ┌──────────────────────────────┐
                    │       Your Task / Prompt      │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │       Complexity Router       │
                    │   LOW → MEDIUM → HIGH         │
                    └──────┬───────┬────────┬──────┘
                           │       │        │
                        Gemini  DeepSeek  Claude
                        Flash     V3      Sonnet
                      (cheapest) (mid)  (+thinking)
                           │       │        │
                    ┌──────▼───────▼────────▼──────┐
                    │       Fallback Chain          │
                    │  model A fails → B → C       │
                    └──────────────────────────────┘


  ┌──────────────────────────────────────────────────┐
  │           Team Code (光明神合体)                  │
  │                                                  │
  │  [Manager]  Claude Sonnet                       │
  │      └─ decomposes task into atomic subtasks    │
  │             │                                   │
  │  [Worker]   DeepSeek V3                         │
  │      └─ executes each subtask                   │
  │             │                                   │
  │  [Reviewer] Gemini Flash                        │
  │      └─ validates → retry if fail (max 2×)      │
  └──────────────────────────────────────────────────┘


  ┌──────────────────────────────────────────────────┐
  │         Model Switch: SHP Handoff                │
  │                                                  │
  │  Old Model → compress history → HandoffPackage  │
  │                                      │           │
  │                               New Model reads   │
  │                               SHP not history   │
  │                                                  │
  │  SHP: goal • validated_skills • failed_approaches│
  │       memory_anchors • next_actions • risk_flags │
  └──────────────────────────────────────────────────┘


  ┌──────────────────────────────────────────────────┐
  │       Capability Prober (Entrance Exam)          │
  │                                                  │
  │  5 atomic probes before trusting a model:       │
  │  ✓ Logic trap detection                         │
  │  ✓ Instruction following (pure JSON output)     │
  │  ✓ Error resilience (API failure handling)      │
  │  ✓ Code generation (syntactic validity)         │
  │  ✓ Multi-step reasoning                         │
  │                                                  │
  │  Output: radar score + role assignment          │
  │  (manager / worker / reviewer / unsuitable)     │
  └──────────────────────────────────────────────────┘
```

---

## Modules

| Module | File | Purpose |
|--------|------|---------|
| **Complexity Router** | `src/router.py` | Score task complexity, select model chain |
| **OpenRouter Client** | `src/client.py` | API calls with fallback + cost tracking |
| **Capability Prober** | `src/prober.py` | Model entrance exam — 5 atomic probes |
| **Handoff Manager** | `src/handoff.py` | Compress history to SHP, inject into new model |
| **Team Code** | `src/agents/team_agent.py` | Manager → Worker → Reviewer pipeline |
| **Skill Registry** | `src/skill_registry.py` | Store and validate reusable skill specs |
| **Code Review Agent** | `src/agents/code_review_agent.py` | Review → Fix → Validate code pipeline |

---

## Quick Start

```bash
git clone https://github.com/your-handle/multi-model-agent-router
cd multi-model-agent-router
pip install -r requirements.txt
cp .env.example .env   # add your OpenRouter key
```

### 1. Basic routing

```python
from src import OpenRouterClient

client = OpenRouterClient()

resp = client.chat("What does zip() do?")                        # → Gemini Flash
resp = client.chat("Write a Redis rate limiter in Python")       # → DeepSeek V3
resp = client.chat("Design a fault-tolerant event sourcing architecture",
                   extended_thinking=True)                       # → Claude Sonnet

print(client.get_stats())
# {'total_calls': 3, 'total_cost_usd': 0.00038, 'fallback_rate': 0.0}
```

### 2. Probe a model before using it

```python
from src.prober import CapabilityProber

prober = CapabilityProber(client)
profile = prober.probe("deepseek-chat")

# ╔══ Capability Profile: DeepSeek V3 ══
#   logic           ██████████  100%
#   instruction     ██████████  100%
#   tool_use        ██████░░░░   60%
#   code_gen        ██████████  100%
#   reasoning       ████████░░   80%
#   OVERALL         █████████░   88%
#   Recommended role: WORKER
```

### 3. Switch models without losing context

```python
from src.handoff import HandoffManager

manager = HandoffManager(client)

# Compress conversation history into a structured package
shp = manager.compress(conversation_history, from_model="claude-sonnet")

# New model reads the SHP — not raw history
response = manager.inject(shp, "Continue the n8n workflow", to_model_key="deepseek-chat")
```

### 4. Team Code — 3 models, 1 result

```python
from src.agents.team_agent import TeamCodeOrchestrator

orchestrator = TeamCodeOrchestrator(client)
result = orchestrator.run(
    "Build a thread-safe Python rate limiter with sliding window algorithm"
)

# Manager:  Claude Sonnet (task decomposition)
# Worker:   DeepSeek V3  (code generation)
# Reviewer: Gemini Flash  (validation + retry)

print(result.summary())
# Subtasks: 3/3 passed | avg score: 87/100
# Cost: $0.00089 | Tokens: 4,231
# Overall: SUCCESS
```

### 5. Validate skills across models

```python
from src.skill_registry import SkillRegistry

registry = SkillRegistry()
result = registry.validate_skill("pure_json_output", client, "deepseek-chat", runs=3)
# {"pass_rate": 1.0, "failures": []}
```

---

## Cost Comparison

| Model | Input /1K | Output /1K | Role |
|-------|-----------|------------|------|
| Gemini 2.0 Flash | $0.0001 | $0.0004 | Reviewer |
| DeepSeek V3 | $0.00027 | $0.0011 | Worker |
| DeepSeek R1 | $0.00055 | $0.00219 | Reasoning fallback |
| Claude Haiku 3.5 | $0.0008 | $0.004 | Fallback worker |
| Claude Sonnet 4.5 | $0.003 | $0.015 | Manager |

A Sonnet + DeepSeek + Gemini team costs **30–50% of Opus 4.6** while reaching comparable output quality on well-defined tasks — the Reviewer catches what the Worker misses, simulating the internal reasoning of a stronger single model.

---

## Project Structure

```
multi-model-agent-router/
├── src/
│   ├── router.py
│   ├── client.py
│   ├── prober.py
│   ├── handoff.py
│   ├── skill_registry.py
│   └── agents/
│       ├── team_agent.py
│       └── code_review_agent.py
├── examples/
│   ├── 01_basic_routing.py
│   ├── 02_code_review_pipeline.py
│   ├── 03_capability_probe.py
│   └── 04_team_code_and_handoff.py
├── requirements.txt
└── .env.example
```

---

## Motivation

Switching AI models mid-project feels like replacing an employee mid-task — the new hire doesn't know what was validated, what failed, or why certain approaches were abandoned. This project treats model switching as a **first-class engineering problem** with a protocol (SHP), a testing layer (Capability Prober), and an orchestration pattern (Team Code) that together make multi-model AI workflows practical for individual developers who can't afford flagship models for everything.

---

## Roadmap

- [ ] Web dashboard for capability radar charts
- [ ] Async client for parallel probe runs
- [ ] Auto team assembly from probe results
- [ ] SKILL.md sharing — export validated skills across projects
- [ ] Cost budget guard with hard-stop threshold

---

## License

MIT
