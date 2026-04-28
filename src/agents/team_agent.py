"""
Team Code Orchestrator
========================
"光明神合体" — combine 3 mid-tier models to approach or exceed a single flagship model.

Roles:
  Manager  (1×) — Task decomposition, SHP maintenance, quality gate
  Worker   (1–2×) — High-volume code/content generation  
  Reviewer (1×) — SKILL validation, error detection, retry arbitration

Architecture:
  User Request
       │
       ▼
   [Manager]  →  decomposes into atomic subtasks
       │
       ▼
   [Worker]   →  executes each subtask
       │
       ▼
   [Reviewer] →  validates against skill spec
       │
    pass? ──No──→ [Worker] retry (max 2×)
       │
      Yes
       │
       ▼
  Final Output
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from .client import OpenRouterClient
from .router import TaskComplexity, MODEL_REGISTRY
from .utils.logger import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────
# System prompts for each role
# ──────────────────────────────────────────────

MANAGER_SYSTEM = """You are a technical project manager and task decomposer.
Your ONLY job is to break down a request into atomic subtasks for a worker agent.

Rules:
- Each subtask must be independently executable (no implicit dependencies on previous steps unless stated)
- Use precise technical language
- Do NOT generate any code or content yourself

Respond ONLY with JSON:
{
  "plan_summary": "one sentence",
  "subtasks": [
    {"id": 1, "title": "...", "instruction": "exact instruction for worker", "depends_on": []},
    {"id": 2, "title": "...", "instruction": "...", "depends_on": [1]}
  ],
  "success_criteria": "how to know the overall task is complete",
  "risk_flags": ["potential issues"]
}"""

WORKER_SYSTEM = """You are a skilled software engineer and content creator.
Execute the given subtask precisely and completely.
Output only what was asked — no preamble, no meta-commentary."""

REVIEWER_SYSTEM = """You are a strict QA engineer and technical reviewer.
Evaluate the worker's output against the given criteria.

Respond ONLY with JSON:
{
  "passed": true/false,
  "score": 0-100,
  "issues": ["issue 1", "issue 2"],
  "fix_instruction": "specific instruction for worker to fix (empty if passed)"
}"""


# ──────────────────────────────────────────────
# Result types
# ──────────────────────────────────────────────

@dataclass
class SubtaskResult:
    subtask_id: int
    title: str
    output: str
    passed: bool
    score: int
    retries: int
    worker_model: str
    reviewer_model: str


@dataclass
class TeamResult:
    final_output: str
    subtask_results: list[SubtaskResult]
    plan_summary: str
    success_criteria: str
    total_cost_usd: float
    total_tokens: int
    manager_model: str
    worker_model: str
    reviewer_model: str
    overall_passed: bool

    def summary(self) -> str:
        passed = sum(1 for r in self.subtask_results if r.passed)
        total = len(self.subtask_results)
        avg_score = sum(r.score for r in self.subtask_results) / total if total else 0
        retries = sum(r.retries for r in self.subtask_results)
        return (
            f"\n╔══ Team Code Result ══════════════════╗\n"
            f"  Plan: {self.plan_summary}\n"
            f"  Subtasks: {passed}/{total} passed  |  avg score: {avg_score:.0f}/100\n"
            f"  Retries triggered: {retries}\n"
            f"  Team: {self.manager_model} → {self.worker_model} → {self.reviewer_model}\n"
            f"  Cost: ${self.total_cost_usd:.5f}  |  Tokens: {self.total_tokens:,}\n"
            f"  Overall: {'✓ SUCCESS' if self.overall_passed else '✗ INCOMPLETE'}\n"
            f"╚══════════════════════════════════════╝"
        )


# ──────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────

class TeamCodeOrchestrator:
    """
    Coordinates Manager → Worker → Reviewer pipeline.
    Each role can use a different model, optimized for cost vs capability.
    """

    MAX_RETRIES = 2

    def __init__(
        self,
        client: OpenRouterClient,
        manager_complexity: TaskComplexity = TaskComplexity.HIGH,
        worker_complexity: TaskComplexity = TaskComplexity.MEDIUM,
        reviewer_complexity: TaskComplexity = TaskComplexity.LOW,
    ):
        self.client = client
        self.manager_complexity = manager_complexity
        self.worker_complexity = worker_complexity
        self.reviewer_complexity = reviewer_complexity

    def _parse_json(self, text: str) -> dict:
        clean = re.sub(r"```(?:json)?|```", "", text).strip()
        return json.loads(clean)

    def _manage(self, task: str) -> tuple[dict, str, float, int]:
        resp = self.client.chat(
            prompt=f"Decompose this task into atomic subtasks:\n\n{task}",
            system=MANAGER_SYSTEM,
            force_complexity=self.manager_complexity,
            max_tokens=1500,
            temperature=0.1,
        )
        try:
            plan = self._parse_json(resp.content)
        except Exception:
            plan = {
                "plan_summary": task[:80],
                "subtasks": [{"id": 1, "title": "Execute task", "instruction": task, "depends_on": []}],
                "success_criteria": "Task completed without errors",
                "risk_flags": [],
            }
        return plan, resp.model_used, resp.estimated_cost_usd, resp.input_tokens + resp.output_tokens

    def _work(self, instruction: str, context: str = "") -> tuple[str, str, float, int]:
        prompt = instruction
        if context:
            prompt = f"Context from previous steps:\n{context}\n\nYour task:\n{instruction}"
        resp = self.client.chat(
            prompt=prompt,
            system=WORKER_SYSTEM,
            force_complexity=self.worker_complexity,
            max_tokens=3000,
            temperature=0.2,
        )
        return resp.content, resp.model_used, resp.estimated_cost_usd, resp.input_tokens + resp.output_tokens

    def _review(self, output: str, instruction: str, criteria: str) -> tuple[dict, str, float, int]:
        prompt = (
            f"Review this output:\n\n---\n{output}\n---\n\n"
            f"Original instruction: {instruction}\n"
            f"Success criteria: {criteria}"
        )
        resp = self.client.chat(
            prompt=prompt,
            system=REVIEWER_SYSTEM,
            force_complexity=self.reviewer_complexity,
            max_tokens=512,
            temperature=0.0,
        )
        try:
            review = self._parse_json(resp.content)
        except Exception:
            review = {"passed": True, "score": 70, "issues": [], "fix_instruction": ""}
        return review, resp.model_used, resp.estimated_cost_usd, resp.input_tokens + resp.output_tokens

    def run(self, task: str, skill_spec: Optional[str] = None) -> TeamResult:
        """
        Execute a task using the full Manager → Worker → Reviewer pipeline.
        skill_spec: optional SKILL.md content to pass as additional review context.
        """
        logger.info(f"=== Team Code: Starting pipeline ===")
        total_cost = 0.0
        total_tokens = 0
        subtask_results: list[SubtaskResult] = []
        completed_outputs: dict[int, str] = {}

        # ── Step 1: Manager decomposes ──
        logger.info("Manager: decomposing task...")
        plan, manager_model, cost, tokens = self._manage(task)
        total_cost += cost
        total_tokens += tokens
        logger.info(f"  Plan: {plan.get('plan_summary', '')} ({len(plan.get('subtasks', []))} subtasks)")

        criteria = plan.get("success_criteria", "task completed correctly")
        if skill_spec:
            criteria += f"\n\nSKILL SPEC:\n{skill_spec}"

        worker_model = ""
        reviewer_model = ""

        # ── Step 2: Worker + Reviewer loop per subtask ──
        for subtask in plan.get("subtasks", []):
            sid = subtask["id"]
            instruction = subtask["instruction"]
            title = subtask.get("title", f"Subtask {sid}")
            logger.info(f"Worker: [{sid}] {title}")

            # Build context from dependencies
            deps = subtask.get("depends_on", [])
            context = "\n\n".join(
                f"[Subtask {d} output]:\n{completed_outputs[d]}"
                for d in deps if d in completed_outputs
            )

            output = ""
            retries = 0
            passed = False
            score = 0

            for attempt in range(self.MAX_RETRIES + 1):
                if attempt > 0:
                    logger.info(f"  ↻ Retry {attempt}/{self.MAX_RETRIES} for subtask {sid}")

                output, worker_model, cost, tokens = self._work(instruction, context)
                total_cost += cost
                total_tokens += tokens

                review, reviewer_model, cost, tokens = self._review(output, instruction, criteria)
                total_cost += cost
                total_tokens += tokens

                passed = review.get("passed", False)
                score = review.get("score", 0)
                icon = "✓" if passed else "✗"
                logger.info(f"  {icon} Reviewer score: {score}/100")

                if passed:
                    break

                # Feed fix instruction back into next attempt
                fix = review.get("fix_instruction", "")
                if fix:
                    instruction = f"{instruction}\n\nFix required: {fix}"
                retries = attempt + 1

            completed_outputs[sid] = output
            subtask_results.append(SubtaskResult(
                subtask_id=sid, title=title, output=output,
                passed=passed, score=score, retries=retries,
                worker_model=worker_model, reviewer_model=reviewer_model,
            ))

        # ── Step 3: Synthesize final output ──
        if len(completed_outputs) == 1:
            final = list(completed_outputs.values())[0]
        else:
            parts = "\n\n".join(
                f"### {r.title}\n{r.output}" for r in subtask_results
            )
            final = parts

        overall_passed = all(r.passed for r in subtask_results)
        result = TeamResult(
            final_output=final,
            subtask_results=subtask_results,
            plan_summary=plan.get("plan_summary", ""),
            success_criteria=criteria,
            total_cost_usd=total_cost,
            total_tokens=total_tokens,
            manager_model=manager_model,
            worker_model=worker_model,
            reviewer_model=reviewer_model,
            overall_passed=overall_passed,
        )
        logger.info(result.summary())
        return result
