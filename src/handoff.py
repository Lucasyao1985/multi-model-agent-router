"""
Structured Handoff Package (SHP)
=================================
When switching models mid-task, don't pass raw conversation history.
Instead, compress state into a structured JSON "handoff package"
that any model can parse regardless of its attention window quirks.

Analogy: An employee leaving doesn't dump their email inbox on the new hire.
They write a handover document.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .client import OpenRouterClient
from .router import TaskComplexity
from .utils.logger import get_logger

logger = get_logger(__name__)


SHP_SUMMARIZER_SYSTEM = """You are a technical project manager creating a handoff document.
Analyze the conversation and extract structured state.

Respond ONLY with this exact JSON format:
{
  "current_goal": "one sentence describing the active objective",
  "progress_pct": 0,
  "validated_skills": ["skill1", "skill2"],
  "failed_approaches": [{"approach": "...", "reason": "..."}],
  "memory_anchors": {"key_name": "key_value"},
  "next_actions": ["step1", "step2", "step3"],
  "open_questions": ["unresolved question 1"],
  "context_files": ["file.py", "config.yaml"],
  "risk_flags": ["thing to avoid"]
}

Be concise. Each field should be actionable for the incoming model."""


@dataclass
class HandoffPackage:
    """Portable state container passed between models."""
    current_goal: str
    progress_pct: int                         # 0–100
    validated_skills: list[str]
    failed_approaches: list[dict]             # [{approach, reason}]
    memory_anchors: dict                      # key facts the new model must know
    next_actions: list[str]
    open_questions: list[str]
    context_files: list[str]
    risk_flags: list[str]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    from_model: str = ""
    to_model: str = ""

    def to_system_prompt(self) -> str:
        """
        Convert SHP into a system prompt prefix for the incoming model.
        This replaces thousands of tokens of raw history with ~200 tokens.
        """
        anchors = "\n".join(f"  - {k}: {v}" for k, v in self.memory_anchors.items())
        skills = ", ".join(self.validated_skills) if self.validated_skills else "none yet"
        failed = "\n".join(
            f"  - {f['approach']}: {f['reason']}" for f in self.failed_approaches
        ) if self.failed_approaches else "  none"
        risks = "\n".join(f"  - {r}" for r in self.risk_flags) if self.risk_flags else "  none"
        next_steps = "\n".join(f"  {i+1}. {a}" for i, a in enumerate(self.next_actions))

        return f"""=== TASK HANDOFF PACKAGE ===
Handed off from: {self.from_model}
Current goal: {self.current_goal}
Progress: {self.progress_pct}%

MEMORY ANCHORS (critical facts — do not re-derive):
{anchors if anchors else "  none"}

VALIDATED SKILLS (already working):
  {skills}

FAILED APPROACHES (do NOT retry these):
{failed}

NEXT ACTIONS:
{next_steps}

RISK FLAGS:
{risks}
=== END HANDOFF ===

You are now the active agent. Continue from where the previous model left off.
Do not re-ask for information already in the anchors above."""

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "HandoffPackage":
        data = json.loads(raw)
        data.pop("created_at", None)
        data.pop("from_model", None)
        data.pop("to_model", None)
        return cls(**data)


class HandoffManager:
    """
    Compresses conversation history into an SHP,
    and injects it as context for the incoming model.
    """

    def __init__(self, client: OpenRouterClient):
        self.client = client
        self._current_shp: Optional[HandoffPackage] = None

    def compress(
        self,
        history: list[dict],        # [{"role": "user"|"assistant", "content": "..."}]
        from_model: str = "unknown",
    ) -> HandoffPackage:
        """
        Feed conversation history → get a compact HandoffPackage back.
        Uses a MEDIUM-complexity model (cost-efficient).
        """
        if not history:
            raise ValueError("History is empty, nothing to compress.")

        # Format history as readable text
        formatted = "\n\n".join(
            f"[{m['role'].upper()}]: {m['content'][:800]}"   # truncate very long turns
            for m in history[-20:]  # last 20 turns max
        )

        prompt = f"Analyze this AI conversation and extract the handoff state:\n\n{formatted}"

        resp = self.client.chat(
            prompt=prompt,
            system=SHP_SUMMARIZER_SYSTEM,
            force_complexity=TaskComplexity.MEDIUM,
            max_tokens=1024,
            temperature=0.1,
        )

        import re
        clean = re.sub(r"```(?:json)?|```", "", resp.content).strip()
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            logger.warning("SHP parse failed, using fallback structure.")
            data = {
                "current_goal": "Unknown — see raw history",
                "progress_pct": 0,
                "validated_skills": [],
                "failed_approaches": [],
                "memory_anchors": {},
                "next_actions": ["Review conversation history and continue"],
                "open_questions": [],
                "context_files": [],
                "risk_flags": ["SHP generation failed — proceed carefully"],
            }

        shp = HandoffPackage(
            from_model=from_model,
            **{k: data.get(k, v) for k, v in {
                "current_goal": "",
                "progress_pct": 0,
                "validated_skills": [],
                "failed_approaches": [],
                "memory_anchors": {},
                "next_actions": [],
                "open_questions": [],
                "context_files": [],
                "risk_flags": [],
            }.items()}
        )
        self._current_shp = shp
        logger.info(f"SHP created: goal='{shp.current_goal}' progress={shp.progress_pct}%")
        return shp

    def inject(
        self,
        shp: HandoffPackage,
        new_prompt: str,
        to_model_key: str,
        base_system: str = "",
    ) -> str:
        """
        Build a combined system prompt = SHP prefix + original system,
        then call the new model with the handoff context.
        Returns the new model's response content.
        """
        shp.to_model = to_model_key
        combined_system = shp.to_system_prompt()
        if base_system:
            combined_system += f"\n\n{base_system}"

        from .router import MODEL_REGISTRY, TaskComplexity
        # Force the specified model by passing it directly
        resp = self.client.chat(
            prompt=new_prompt,
            system=combined_system,
            force_complexity=TaskComplexity.HIGH,   # new model onboarding — don't cheap out
            max_tokens=2048,
        )
        logger.info(f"Handoff to {resp.model_used} complete. Tokens: {resp.input_tokens + resp.output_tokens}")
        return resp.content
