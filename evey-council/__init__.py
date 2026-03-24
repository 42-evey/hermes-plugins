"""Evey Council Plugin — Model Council for high-stakes decisions.

Sends a question to 3 free models in parallel, collects answers, then
has a judge model synthesize the best answer from all three. Gives Evey
a "think harder" capability at zero cost.

Unique to Evey: no other agent has multi-model deliberation built in.
"""

import concurrent.futures
import json
import logging
import os
import time

import importlib.util as _iu, os as _os
_spec = _iu.spec_from_file_location("evey_utils", _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "evey_utils.py"))
_eu = _iu.module_from_spec(_spec)
_spec.loader.exec_module(_eu)
call_model = _eu.call_model

logger = logging.getLogger("evey.council")

# Council members — all FREE cloud models
COUNCIL_MODELS = [
    "mimo-v2-pro",
    "llama70b-free",
    "qwen-coder-free",
]

# Judge — synthesizes the best answer
JUDGE_MODEL = "mimo-v2-pro"

MAX_RETRIES = 2

SCHEMA = {
    "name": "council_decide",
    "description": (
        "Convene a Model Council for important decisions. Sends the question to "
        "3 free models simultaneously, collects all answers, then a judge model "
        "synthesizes the best answer from all three. Use for high-stakes questions, "
        "contested topics, or when you want a higher-quality answer than any single "
        "model provides. Zero cost — all free models."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question or task for the council to deliberate on",
            },
            "context": {
                "type": "string",
                "description": "Additional context to include (optional)",
            },
        },
        "required": ["question"],
    },
}


def _call_council_model(model, prompt, max_tokens=2000, temperature=0.7):
    """Call a model via shared evey_utils. Returns (content, model) or raises."""
    result = call_model(model, prompt, max_tokens=max_tokens, temperature=temperature, retries=MAX_RETRIES, timeout=120)
    if result is None:
        raise RuntimeError(f"{model} failed after {MAX_RETRIES} retries")
    return result["content"], model


def _query_council_member(model, question, context):
    """Query one council member. Returns dict with model, answer, or error."""
    prompt = question
    if context:
        prompt = f"CONTEXT:\n{context}\n\nQUESTION:\n{question}"

    try:
        content, used_model = _call_council_model(model, prompt)
        return {"model": used_model, "status": "success", "answer": content}
    except Exception as e:
        logger.warning(f"Council member {model} failed: {e}")
        return {"model": model, "status": "failed", "error": str(e)[:200]}


def _judge_answers(question, context, answers):
    """Have the judge synthesize the best answer from all council responses."""
    successful = [a for a in answers if a["status"] == "success"]

    if not successful:
        return None, "All council members failed"

    if len(successful) == 1:
        # Only one answer — return it directly, no judging needed
        return successful[0]["answer"], f"Only 1/{len(answers)} models responded"

    # Build the judge prompt
    judge_prompt = (
        "You are a judge synthesizing the best answer from multiple AI models.\n\n"
        f"QUESTION: {question}\n"
    )
    if context:
        judge_prompt += f"CONTEXT: {context}\n"

    judge_prompt += "\n"
    for i, a in enumerate(successful, 1):
        judge_prompt += f"--- MODEL {i} ({a['model']}) ---\n{a['answer']}\n\n"

    judge_prompt += (
        "--- YOUR TASK ---\n"
        "Synthesize the best answer by:\n"
        "1. Taking the strongest points from each model\n"
        "2. If models disagree, explain why and pick the best reasoning\n"
        "3. If one model clearly outperforms, say so and use their answer as the base\n"
        "4. Be concise — give the synthesized answer, not a meta-commentary\n\n"
        "SYNTHESIZED ANSWER:"
    )

    try:
        content, _ = _call_council_model(JUDGE_MODEL, judge_prompt, max_tokens=3000, temperature=0.3)
        return content, None
    except Exception as e:
        # Judge failed — fall back to longest successful answer (heuristic: more detail = better)
        best = max(successful, key=lambda a: len(a["answer"]))
        return best["answer"], f"Judge failed ({e}), using best individual answer from {best['model']}"


def handler(args, **kwargs):
    try:
        question = args.get("question", "")
        context = args.get("context", "")

        if not question:
            return json.dumps({"status": "error", "error": "No question provided"})

        logger.info(f"Council convened for: {question[:80]}...")

        # Phase 1: Query all council members in parallel
        start_time = time.time()
        answers = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(_query_council_member, model, question, context): model
                for model in COUNCIL_MODELS
            }
            for future in concurrent.futures.as_completed(futures):
                answers.append(future.result())

        council_time = time.time() - start_time
        succeeded = sum(1 for a in answers if a["status"] == "success")
        logger.info(f"Council phase 1 complete: {succeeded}/{len(answers)} in {council_time:.1f}s")

        # Phase 2: Judge synthesizes the best answer
        judge_start = time.time()
        synthesized, judge_note = _judge_answers(question, context, answers)
        judge_time = time.time() - judge_start

        if synthesized is None:
            return json.dumps({
                "status": "failed",
                "error": judge_note or "Council failed completely",
                "models_queried": [a["model"] for a in answers],
                "individual_errors": [a.get("error", "") for a in answers if a["status"] == "failed"],
            })

        result = {
            "status": "success",
            "synthesized_answer": synthesized,
            "council_members": len(COUNCIL_MODELS),
            "members_responded": succeeded,
            "models_queried": [a["model"] for a in answers],
            "council_time_sec": round(council_time, 1),
            "judge_time_sec": round(judge_time, 1),
            "total_time_sec": round(council_time + judge_time, 1),
            "cost": "free",
        }

        if judge_note:
            result["judge_note"] = judge_note

        # Include individual answers for transparency
        result["individual_answers"] = [
            {
                "model": a["model"],
                "status": a["status"],
                "answer_preview": a.get("answer", "")[:200] if a.get("answer") else None,
                "error": a.get("error"),
            }
            for a in answers
        ]

        logger.info(f"Council decision complete in {result['total_time_sec']}s")
        return json.dumps(result)

    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


def register(ctx):
    ctx.register_tool(
        name="council_decide",
        toolset="evey_council",
        schema=SCHEMA,
        handler=handler,
    )
