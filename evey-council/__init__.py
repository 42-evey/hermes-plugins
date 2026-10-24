"""Evey Council Plugin — Model Council for high-stakes decisions.

Sends a question to 3 council members in parallel, each with its own role
(pragmatist, skeptic, contrarian), collects answers, then has a judge
synthesize the best answer from all three. Gives Evey a "think harder"
capability.

Every call goes through Hermes (ctx.llm) on the user's active provider and
model, so it needs no keys or model configuration.
"""

import concurrent.futures
import json
import logging
import os
import time

logger = logging.getLogger("evey.council")

_CTX = None  # set by register(); LLM calls go through ctx.llm

# Council members — same active model, different roles
COUNCIL_ROLES = [
    ("pragmatist", "You are a pragmatic engineer on a decision council. Give the most practical, "
                   "proven answer and its main trade-offs."),
    ("skeptic", "You are the skeptic on a decision council. Look for risks, edge cases and failure "
                "modes first, then give your answer."),
    ("contrarian", "You are the contrarian on a decision council. Make the strongest case for the "
                   "option others are likely to overlook, then give your answer."),
]

# Judge — synthesizes the best answer
JUDGE_SYSTEM = "You are a judge synthesizing the best answer from multiple council members."

MAX_RETRIES = 2

SCHEMA = {
    "name": "council_decide",
    "description": (
        "Convene a Model Council for important decisions. Sends the question to "
        "3 council members (pragmatist, skeptic, contrarian) simultaneously, collects "
        "all answers, then a judge synthesizes the best answer from all three. Use for "
        "high-stakes questions, contested topics, or when you want a higher-quality "
        "answer than a single pass provides."
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


def _call_council_model(system, prompt, max_tokens=2000, temperature=0.7):
    """One ctx.llm call on the active model. Returns (content, model) or raises."""
    if _CTX is None:
        raise RuntimeError("plugin not registered")
    last_error = "empty response"
    for _ in range(MAX_RETRIES + 1):
        try:
            result = _CTX.llm.complete(
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=300,
                purpose="evey-council",
            )
        except Exception as e:
            last_error = str(e)
            continue
        if (result.text or "").strip():
            return result.text.strip(), result.model
    raise RuntimeError(f"failed after {MAX_RETRIES + 1} attempts: {last_error}")


def _query_council_member(role, system, question, context):
    """Query one council member. Returns dict with member, model, answer, or error."""
    prompt = question
    if context:
        prompt = f"CONTEXT:\n{context}\n\nQUESTION:\n{question}"

    try:
        content, used_model = _call_council_model(system, prompt)
        return {"member": role, "model": used_model, "status": "success", "answer": content}
    except Exception as e:
        logger.warning(f"Council member {role} failed: {e}")
        return {"member": role, "model": None, "status": "failed", "error": str(e)[:200]}


def _judge_answers(question, context, answers):
    """Have the judge synthesize the best answer from all council responses."""
    successful = [a for a in answers if a["status"] == "success"]

    if not successful:
        return None, "All council members failed"

    if len(successful) == 1:
        # Only one answer — return it directly, no judging needed
        return successful[0]["answer"], f"Only 1/{len(answers)} models responded"

    # Build the judge prompt
    judge_prompt = f"QUESTION: {question}\n"
    if context:
        judge_prompt += f"CONTEXT: {context}\n"

    judge_prompt += "\n"
    for i, a in enumerate(successful, 1):
        judge_prompt += f"--- MEMBER {i} ({a['member']}) ---\n{a['answer']}\n\n"

    judge_prompt += (
        "--- YOUR TASK ---\n"
        "Synthesize the best answer by:\n"
        "1. Taking the strongest points from each member\n"
        "2. If members disagree, explain why and pick the best reasoning\n"
        "3. If one member clearly outperforms, say so and use their answer as the base\n"
        "4. Be concise — give the synthesized answer, not a meta-commentary\n\n"
        "SYNTHESIZED ANSWER:"
    )

    try:
        content, _ = _call_council_model(JUDGE_SYSTEM, judge_prompt, max_tokens=3000, temperature=0.3)
        return content, None
    except Exception as e:
        # Judge failed — fall back to longest successful answer (heuristic: more detail = better)
        best = max(successful, key=lambda a: len(a["answer"]))
        return best["answer"], f"Judge failed ({e}), using best individual answer from {best['member']}"


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
                pool.submit(_query_council_member, role, system, question, context): role
                for role, system in COUNCIL_ROLES
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
                "members_queried": [a["member"] for a in answers],
                "individual_errors": [a.get("error", "") for a in answers if a["status"] == "failed"],
            })

        result = {
            "status": "success",
            "synthesized_answer": synthesized,
            "council_members": len(COUNCIL_ROLES),
            "members_responded": succeeded,
            "members_queried": [a["member"] for a in answers],
            "models_used": sorted({a["model"] for a in answers if a.get("model")}),
            "council_time_sec": round(council_time, 1),
            "judge_time_sec": round(judge_time, 1),
            "total_time_sec": round(council_time + judge_time, 1),
        }

        if judge_note:
            result["judge_note"] = judge_note

        # Include individual answers for transparency
        result["individual_answers"] = [
            {
                "member": a["member"],
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
    global _CTX
    _CTX = ctx
    ctx.register_tool(
        name="council_decide",
        toolset="evey_council",
        schema=SCHEMA,
        handler=handler,
    )
