"""
CodeReviewAgent
Long-chain reasoning pipeline: Review → Fix → Test Validation
Each step routes to the optimal model for its complexity profile.
"""

import json
import re
from dataclasses import dataclass, field

from ..client import OpenRouterClient, AgentResponse
from ..router import TaskComplexity
from ..utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ReviewResult:
    issues: list[dict]          # [{severity, location, description, suggestion}]
    summary: str
    risk_level: str             # low / medium / high
    raw_response: str


@dataclass
class FixResult:
    fixed_code: str
    changes_made: list[str]
    raw_response: str


@dataclass
class ValidationResult:
    passed: bool
    test_cases: list[dict]      # [{name, expected, result, passed}]
    coverage_estimate: str
    raw_response: str


@dataclass
class PipelineResult:
    review: ReviewResult
    fix: FixResult
    validation: ValidationResult
    total_cost_usd: float
    models_used: list[str] = field(default_factory=list)
    total_tokens: int = 0


REVIEW_SYSTEM = """You are a senior code reviewer. Analyze code for bugs, security issues,
performance problems, and style violations.

Respond ONLY with valid JSON in this exact format:
{
  "issues": [
    {
      "severity": "critical|high|medium|low",
      "location": "line X or function name",
      "description": "what is wrong",
      "suggestion": "how to fix it"
    }
  ],
  "summary": "overall assessment in 1-2 sentences",
  "risk_level": "low|medium|high"
}"""

FIX_SYSTEM = """You are an expert software engineer. Given code and a list of review issues,
produce the corrected version.

Respond ONLY with valid JSON:
{
  "fixed_code": "the complete corrected code here",
  "changes_made": ["change 1 description", "change 2 description"]
}"""

VALIDATION_SYSTEM = """You are a QA engineer. Given original code, fixed code, and the issues
that were fixed, generate test cases to validate the fixes.

Respond ONLY with valid JSON:
{
  "test_cases": [
    {
      "name": "test name",
      "expected": "expected behavior",
      "result": "pass|fail|uncertain",
      "passed": true
    }
  ],
  "coverage_estimate": "e.g. 80%",
  "overall_passed": true
}"""


def _parse_json_response(raw: str) -> dict:
    """Strip markdown fences and parse JSON."""
    clean = re.sub(r"```(?:json)?|```", "", raw).strip()
    return json.loads(clean)


class CodeReviewAgent:
    def __init__(self, client: OpenRouterClient):
        self.client = client

    def review(self, code: str, language: str = "python") -> tuple[ReviewResult, AgentResponse]:
        prompt = f"Review this {language} code:\n\n```{language}\n{code}\n```"
        resp = self.client.chat(
            prompt=prompt,
            system=REVIEW_SYSTEM,
            force_complexity=TaskComplexity.HIGH,  # Always use best model for review
            extended_thinking=True,
            max_tokens=2048,
            temperature=0.1,
        )
        try:
            data = _parse_json_response(resp.content)
            result = ReviewResult(
                issues=data.get("issues", []),
                summary=data.get("summary", ""),
                risk_level=data.get("risk_level", "medium"),
                raw_response=resp.content,
            )
        except json.JSONDecodeError:
            result = ReviewResult(issues=[], summary=resp.content, risk_level="unknown", raw_response=resp.content)
        return result, resp

    def fix(self, code: str, review: ReviewResult, language: str = "python") -> tuple[FixResult, AgentResponse]:
        issues_text = json.dumps(review.issues, indent=2)
        prompt = (
            f"Fix this {language} code based on the review issues.\n\n"
            f"Original code:\n```{language}\n{code}\n```\n\n"
            f"Issues to fix:\n{issues_text}"
        )
        resp = self.client.chat(
            prompt=prompt,
            system=FIX_SYSTEM,
            force_complexity=TaskComplexity.MEDIUM,
            max_tokens=4096,
            temperature=0.1,
        )
        try:
            data = _parse_json_response(resp.content)
            result = FixResult(
                fixed_code=data.get("fixed_code", code),
                changes_made=data.get("changes_made", []),
                raw_response=resp.content,
            )
        except json.JSONDecodeError:
            result = FixResult(fixed_code=code, changes_made=[], raw_response=resp.content)
        return result, resp

    def validate(
        self, original: str, fixed: FixResult, review: ReviewResult, language: str = "python"
    ) -> tuple[ValidationResult, AgentResponse]:
        prompt = (
            f"Validate these {language} fixes.\n\n"
            f"Original:\n```{language}\n{original}\n```\n\n"
            f"Fixed:\n```{language}\n{fixed.fixed_code}\n```\n\n"
            f"Issues addressed: {json.dumps([i['description'] for i in review.issues], indent=2)}"
        )
        resp = self.client.chat(
            prompt=prompt,
            system=VALIDATION_SYSTEM,
            force_complexity=TaskComplexity.MEDIUM,
            max_tokens=2048,
            temperature=0.1,
        )
        try:
            data = _parse_json_response(resp.content)
            result = ValidationResult(
                passed=data.get("overall_passed", False),
                test_cases=data.get("test_cases", []),
                coverage_estimate=data.get("coverage_estimate", "unknown"),
                raw_response=resp.content,
            )
        except json.JSONDecodeError:
            result = ValidationResult(passed=False, test_cases=[], coverage_estimate="unknown", raw_response=resp.content)
        return result, resp

    def run_pipeline(self, code: str, language: str = "python") -> PipelineResult:
        """
        Full pipeline: Review → Fix → Validate
        Automatically routes each step to optimal model.
        """
        logger.info("=== Starting Code Review Pipeline ===")
        total_cost = 0.0
        models_used = []
        total_tokens = 0

        logger.info("Step 1/3: Reviewing code...")
        review, r1 = self.review(code, language)
        total_cost += r1.estimated_cost_usd
        total_tokens += r1.input_tokens + r1.output_tokens
        models_used.append(r1.model_used)
        logger.info(f"  Found {len(review.issues)} issues (risk: {review.risk_level})")

        logger.info("Step 2/3: Fixing issues...")
        fix, r2 = self.fix(code, review, language)
        total_cost += r2.estimated_cost_usd
        total_tokens += r2.input_tokens + r2.output_tokens
        models_used.append(r2.model_used)
        logger.info(f"  Applied {len(fix.changes_made)} changes")

        logger.info("Step 3/3: Validating fixes...")
        validation, r3 = self.validate(code, fix, review, language)
        total_cost += r3.estimated_cost_usd
        total_tokens += r3.input_tokens + r3.output_tokens
        models_used.append(r3.model_used)
        logger.info(f"  Validation {'✓ PASSED' if validation.passed else '✗ FAILED'}")

        logger.info(f"=== Pipeline complete | ${total_cost:.5f} | {total_tokens} tokens ===")

        return PipelineResult(
            review=review,
            fix=fix,
            validation=validation,
            total_cost_usd=total_cost,
            models_used=models_used,
            total_tokens=total_tokens,
        )
