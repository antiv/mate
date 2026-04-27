"""
EvalRunner — scores agent outputs against test cases.

Supports three eval methods:
  exact_match  — normalised string equality (1.0 / 0.0)
  semantic     — difflib token overlap, upgrades to sentence-transformers if installed
  llm_judge    — litellm.completion() call, model and threshold from test case config
"""

import difflib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

JUDGE_PROMPT_TEMPLATE = """\
You are an impartial evaluator assessing an AI agent's response quality.

User input: {input}
Expected output: {expected}
Actual output: {actual}

Respond with ONLY a JSON object on a single line:
{{"score": <float 0.0-1.0>, "reasoning": "<one sentence>"}}

Where 1.0 = perfect match of intent and accuracy, 0.0 = completely wrong or irrelevant.\
"""


@dataclass
class EvalRunResult:
    test_case_id: int
    version_id: int
    actual_output: Optional[str]
    score: Optional[float]
    passed: Optional[bool]
    eval_method: str
    details: Optional[str] = None
    error: Optional[str] = None
    run_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EvalRunner:

    @staticmethod
    def exact_match_eval(expected: str, actual: str) -> float:
        return 1.0 if expected.strip().lower() == actual.strip().lower() else 0.0

    @staticmethod
    def semantic_similarity_eval(expected: str, actual: str) -> float:
        try:
            from sentence_transformers import SentenceTransformer, util  # type: ignore
            model = SentenceTransformer('all-MiniLM-L6-v2')
            emb_e = model.encode(expected, convert_to_tensor=True)
            emb_a = model.encode(actual, convert_to_tensor=True)
            return float(util.cos_sim(emb_e, emb_a)[0][0])
        except ImportError:
            pass
        return difflib.SequenceMatcher(None, expected.lower(), actual.lower()).ratio()

    @staticmethod
    def llm_judge_eval(input_text: str, expected: str, actual: str, model: str) -> tuple[float, str]:
        """Returns (score, reasoning). Raises on model error."""
        import litellm  # type: ignore

        prompt = JUDGE_PROMPT_TEMPLATE.format(
            input=input_text, expected=expected, actual=actual
        )
        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=200,
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            parts = content.split("```")
            content = parts[1] if len(parts) > 1 else content
            if content.startswith("json"):
                content = content[4:]
        parsed = json.loads(content.strip())
        score = max(0.0, min(1.0, float(parsed["score"])))
        reasoning = parsed.get("reasoning", "")
        return score, reasoning

    def score_output(self, test_case, actual_output: str, version_id: int) -> EvalRunResult:
        """
        Score actual_output against test_case.expected_output using the configured
        eval method. Returns an EvalRunResult (not the ORM model).
        """
        method = test_case.eval_method
        threshold = float(test_case.threshold) if test_case.threshold is not None else 0.7

        try:
            if method == "exact_match":
                score = self.exact_match_eval(test_case.expected_output, actual_output)
                details = "exact_match"
            elif method == "semantic":
                score = self.semantic_similarity_eval(test_case.expected_output, actual_output)
                details = f"semantic_similarity={score:.3f}"
            elif method == "llm_judge":
                judge_model = test_case.judge_model or "gemini/gemini-2.0-flash"
                score, reasoning = self.llm_judge_eval(
                    test_case.input, test_case.expected_output, actual_output, judge_model
                )
                details = f"score={score:.3f} model={judge_model} reasoning={reasoning}"
            else:
                score = 0.0
                details = f"unknown eval_method: {method}"

            return EvalRunResult(
                test_case_id=test_case.id,
                version_id=version_id,
                actual_output=actual_output,
                score=score,
                passed=score >= threshold,
                eval_method=method,
                details=details,
            )
        except Exception as e:
            logger.error("EvalRunner error for test_case %s: %s", test_case.id, e)
            return EvalRunResult(
                test_case_id=test_case.id,
                version_id=version_id,
                actual_output=actual_output,
                score=None,
                passed=None,
                eval_method=method,
                error=str(e),
            )
