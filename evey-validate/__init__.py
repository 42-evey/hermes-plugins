"""Evey Validate Plugin — output validation for delegation results.

Free models hallucinate more than paid ones. Before trusting delegation
results, run them through validation checks:
1. Confidence scoring (does the model hedge or claim certainty?)
2. Consistency check (do different parts of the response contradict?)
3. Source verification hint (does it cite checkable facts?)
4. Format compliance (does it match what was asked?)

Uses cheap local model for validation — $0 cost.
"""

import json
import os
import re

import importlib.util as _iu, os as _os
_spec = _iu.spec_from_file_location("evey_utils", _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "evey_utils.py"))
_eu = _iu.module_from_spec(_spec)
_spec.loader.exec_module(_eu)
call_llm = _eu.call_llm

VALIDATE_MODEL = "qwen35-4b"

SCHEMA = {
    "name": "validate_output",
    "description": (
        "Validate a delegation result before trusting it. "
        "Checks for hallucination signals, contradictions, and confidence. "
        "Use after delegate_with_model to verify the subagent's output. "
        "Returns: confidence score (0-10), issues found, recommendation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "What was the original task?",
            },
            "result": {
                "type": "string",
                "description": "The delegation result to validate",
            },
            "model_used": {
                "type": "string",
                "description": "Which model produced this result",
            },
        },
        "required": ["task", "result"],
    },
}

# Red flags that indicate hallucination
HALLUCINATION_PATTERNS = [
    (r"(?i)as of my (last |knowledge )?cut.?off", "knowledge cutoff reference"),
    (r"(?i)I (don't|cannot|can't) (access|browse|search)", "capability denial while giving specifics"),
    (r"(?i)(?:January|February|March|April|May) 20[0-9]{2}", "specific date claim — verify"),
    (r"(?i)version \d+\.\d+\.\d+", "specific version number — verify"),
    (r"(?i)according to (?:the|a) (?:official|latest)", "vague authority claim"),
    (r"(?i)it is (?:widely|generally|commonly) (?:known|accepted|believed)", "weasel words"),
]

VALIDATE_PROMPT = """Rate this AI-generated response on a scale of 0-10 for reliability.

TASK: {task}
MODEL: {model}
RESPONSE: {result}

Score criteria:
- 10: Verifiable facts with sources, no hedging
- 7-9: Mostly reliable, minor uncertainties acknowledged
- 4-6: Mix of facts and speculation, some claims unverifiable
- 1-3: Mostly speculation, contradictions, or hallucination signals
- 0: Complete fabrication

Respond with ONLY: SCORE: N | ISSUES: brief description
Example: SCORE: 7 | ISSUES: Version number unverified, otherwise solid"""


def _validate_llm(prompt):
    result = call_llm(VALIDATE_MODEL, prompt, max_tokens=100, temperature=0)
    return result or "SCORE: 5 | ISSUES: Validation unavailable"


def handler(args, **kwargs):
    try:
        task = args.get("task", "")
        result = args.get("result", "")
        model_used = args.get("model_used", "unknown")

        # Pattern-based checks
        flags = []
        for pattern, desc in HALLUCINATION_PATTERNS:
            if re.search(pattern, result):
                flags.append(desc)

        # LLM-based validation
        prompt = VALIDATE_PROMPT.format(
            task=task, result=result[:1500], model=model_used
        )
        llm_response = _validate_llm(prompt)

        # Parse score
        score_match = re.search(r"(?i)score:\s*(\d+)", llm_response)
        score = int(score_match.group(1)) if score_match else 5

        # Adjust score based on pattern flags
        if flags:
            score = max(0, score - len(flags))

        # Recommendation
        if score >= 7:
            recommendation = "TRUST — output looks reliable"
        elif score >= 4:
            recommendation = "CAUTION — verify key claims before using"
        else:
            recommendation = "REJECT — high hallucination risk, re-delegate with better model"

        return json.dumps({
            "score": score,
            "recommendation": recommendation,
            "llm_assessment": llm_response,
            "pattern_flags": flags,
            "model_used": model_used,
        })

    except Exception as e:
        return json.dumps({"error": str(e), "score": 5, "recommendation": "CAUTION — validation error"})


def register(ctx):
    ctx.register_tool(
        name="validate_output",
        toolset="evey_validate",
        schema=SCHEMA,
        handler=handler,
    )
