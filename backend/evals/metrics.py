"""Evaluation metrics used by the API's /api/eval endpoints (model-judged, DeepEval).

They live here, not in the tests, so the application never imports a test file. The judge model is built
only when an evaluation is requested, so the API starts without a key. These metrics cost tokens: golden-customer
checks (eval/) are the cheap first line, this is the optional second.
"""
import os
from functools import lru_cache


@lru_cache(maxsize=1)
def _judge():
    from deepeval.models import GeminiModel
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise EnvironmentError("GOOGLE_API_KEY is not set in .env")
    return GeminiModel(model=os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash"), api_key=api_key, temperature=float(os.getenv("GEMINI_TEMPERATURE", "0")))


@lru_cache(maxsize=1)
def get_metrics():
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCaseParams
    GEMINI_JUDGE = _judge()
    fraud_risk_accuracy = GEval(
        model=GEMINI_JUDGE,
        name="FraudRiskAccuracy",
        criteria="""
        Evaluate ONLY whether the risk level and file_sar match the expected_output values:
        1. Compare actual risk_level to expected_output["risk_level"] — they must match exactly
        2. If expected file_sar_expected=true, actual file_sar must be true
        3. If expected file_sar_expected=false, actual file_sar must be false
        4. DB floor rule: if DB is_flagged=True and DB risk_level=CRITICAL, output MUST be CRITICAL
        5. Ignore all specific indicator details — only risk_level and file_sar matter

        Score 1.0 if risk_level matches expected AND file_sar matches expected.
        Score 0.5 if risk_level is one level off (e.g. CRITICAL vs HIGH).
        Score 0.0 if risk_level is drastically wrong (e.g. LOW when CRITICAL expected).
        """,
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.CONTEXT,
        ],
        threshold=0.7,
    )
    recommendation_actionability = GEval(
        model=GEMINI_JUDGE,
        name="RecommendationActionability",
        criteria="""
        For each recommendation:
        1. Specific — names a concrete action (e.g. "File a suspicious activity report with the financial intelligence unit within the legal deadline")
        2. Not generic (reject: "investigate further", "monitor account")
        3. Proportional to risk level — CRITICAL must include freeze/SAR/escalation
        4. At least one regulatory/compliance action for HIGH or CRITICAL risk
        Score 0 if recommendations are vague or missing for HIGH/CRITICAL.
        """,
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
        ],
        threshold=0.70,
    )
    return [fraud_risk_accuracy, recommendation_actionability]
