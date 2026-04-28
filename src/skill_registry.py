"""
Skill Registry
==============
Store, retrieve, and validate AI agent skills.

A "skill" is a validated prompt pattern that reliably produces
a specific output across model switches.

SKILL.md format (per skill):
  - prompt_baseline : minimal effective instruction
  - expected_format : JSON schema or regex of expected output
  - edge_cases      : known failure modes to test
  - validated_on    : list of models where this skill passed
  - tags            : searchable categories
"""

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .client import OpenRouterClient
from .router import TaskComplexity
from .utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Skill:
    id: str
    name: str
    description: str
    prompt_baseline: str
    expected_format: str          # regex pattern or "json" or "contains:<keyword>"
    edge_cases: list[str]
    tags: list[str]
    validated_on: list[str] = field(default_factory=list)
    pass_rate: float = 0.0
    last_tested: str = ""

    def to_markdown(self) -> str:
        validated = ", ".join(self.validated_on) if self.validated_on else "none yet"
        edges = "\n".join(f"  - {e}" for e in self.edge_cases)
        return f"""## SKILL: {self.name}

**ID**: `{self.id}`  
**Tags**: {", ".join(self.tags)}  
**Pass rate**: {self.pass_rate:.0%} (tested on: {validated})

### Prompt Baseline
```
{self.prompt_baseline}
```

### Expected Format
```
{self.expected_format}
```

### Edge Cases
{edges}
"""


class SkillRegistry:
    """
    In-memory + JSON-file skill store.
    Supports skill validation runs against any model.
    """

    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = Path(storage_path) if storage_path else None
        self._skills: dict[str, Skill] = {}
        self._load_builtins()
        if self.storage_path and self.storage_path.exists():
            self._load_from_file()

    def _load_builtins(self):
        """Pre-load a set of battle-tested skills."""
        builtins = [
            Skill(
                id="pure_json_output",
                name="Pure JSON Output",
                description="Model outputs only valid JSON with no markdown or prose.",
                prompt_baseline='Return a JSON object with key "status" set to "ok".',
                expected_format="json",
                edge_cases=["Model adds ```json fence", "Model adds explanation before JSON"],
                tags=["output_control", "format", "critical"],
            ),
            Skill(
                id="code_python_function",
                name="Python Function Generation",
                description="Generate a syntactically valid Python function.",
                prompt_baseline="Write a Python function `add(a, b)` that returns the sum.",
                expected_format="contains:def add",
                edge_cases=["Missing return statement", "Wrong function name", "Added class wrapper"],
                tags=["code_gen", "python"],
            ),
            Skill(
                id="bug_identification",
                name="Bug Identification",
                description="Correctly identify the presence/absence of a bug in code.",
                prompt_baseline="Does this code have a bug? Answer yes or no and explain.",
                expected_format="contains:yes|contains:no",
                edge_cases=["Hallucinates non-existent bug", "Misses obvious off-by-one"],
                tags=["code_review", "logic"],
            ),
            Skill(
                id="instruction_following_strict",
                name="Strict Instruction Following",
                description="Model follows a multi-constraint instruction exactly.",
                prompt_baseline=(
                    "List exactly 3 animals. Use a numbered list. "
                    "Each animal name must be capitalized. No explanations."
                ),
                expected_format=r"1\.",
                edge_cases=["Outputs 4+ items", "Adds prose before list", "Lowercase names"],
                tags=["instruction", "format", "critical"],
            ),
            Skill(
                id="error_recovery",
                name="Error Recovery",
                description="Model proposes a recovery action given a tool/API error.",
                prompt_baseline=(
                    "The API returned HTTP 503. What should the agent do? "
                    'Answer with one of: "retry", "abort", "fallback".'
                ),
                expected_format="contains:retry|contains:abort|contains:fallback",
                edge_cases=["Gives long explanation instead of single word", "Hallucinates new API"],
                tags=["tool_use", "resilience"],
            ),
        ]
        for skill in builtins:
            self._skills[skill.id] = skill

    def _load_from_file(self):
        try:
            data = json.loads(self.storage_path.read_text())
            for s in data:
                self._skills[s["id"]] = Skill(**s)
            logger.info(f"Loaded {len(data)} skills from {self.storage_path}")
        except Exception as e:
            logger.warning(f"Could not load skills file: {e}")

    def save(self):
        if self.storage_path:
            self.storage_path.write_text(
                json.dumps([s.__dict__ for s in self._skills.values()], indent=2)
            )

    def add(self, skill: Skill):
        self._skills[skill.id] = skill
        logger.info(f"Registered skill: {skill.id}")

    def get(self, skill_id: str) -> Optional[Skill]:
        return self._skills.get(skill_id)

    def search(self, tag: str) -> list[Skill]:
        return [s for s in self._skills.values() if tag in s.tags]

    def all_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def validate_skill(
        self,
        skill_id: str,
        client: OpenRouterClient,
        model_key: str,
        runs: int = 3,
    ) -> dict:
        """
        Run a skill's prompt baseline N times against a model.
        Returns {passed, pass_rate, failures}.
        """
        skill = self._skills.get(skill_id)
        if not skill:
            raise ValueError(f"Unknown skill: {skill_id}")

        from .router import MODEL_REGISTRY
        model_name = MODEL_REGISTRY[model_key].display_name if model_key in MODEL_REGISTRY else model_key

        logger.info(f"Validating skill '{skill.name}' on {model_name} ({runs} runs)...")
        passes = 0
        failures = []

        for i in range(runs):
            try:
                resp = client.chat(
                    prompt=skill.prompt_baseline,
                    force_complexity=TaskComplexity.LOW,
                    max_tokens=512,
                    temperature=0.0,
                )
                output = resp.content

                if skill.expected_format == "json":
                    passed = _is_json(output)
                elif skill.expected_format.startswith("contains:"):
                    patterns = skill.expected_format.replace("contains:", "").split("|")
                    passed = any(p.lower() in output.lower() for p in patterns)
                else:
                    passed = bool(re.search(skill.expected_format, output, re.IGNORECASE))

                if passed:
                    passes += 1
                else:
                    failures.append(f"Run {i+1}: output didn't match '{skill.expected_format}'")
            except Exception as e:
                failures.append(f"Run {i+1}: exception — {e}")

        pass_rate = passes / runs
        if model_name not in skill.validated_on and pass_rate >= 0.8:
            skill.validated_on.append(model_name)
        skill.pass_rate = pass_rate
        skill.last_tested = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        result = {"skill": skill.name, "model": model_name, "pass_rate": pass_rate, "failures": failures}
        logger.info(f"  Result: {passes}/{runs} passed ({pass_rate:.0%})")
        return result

    def validate_all(self, client: OpenRouterClient, model_key: str) -> list[dict]:
        return [self.validate_skill(sid, client, model_key) for sid in self._skills]

    def export_markdown(self) -> str:
        header = "# Skill Registry\n\nValidated AI agent skills.\n\n"
        return header + "\n---\n\n".join(s.to_markdown() for s in self._skills.values())


def _is_json(text: str) -> bool:
    clean = re.sub(r"```(?:json)?|```", "", text).strip()
    try:
        json.loads(clean)
        return True
    except Exception:
        return False
